"""Region fan-out and check execution.

Every AWS call made from here is read-only. The engine never mutates anything
and never runs generated remediation.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field

import boto3
import botocore.exceptions

from zombiescan import packs
from zombiescan.models import Finding, ScanContext
from zombiescan.pricing import PriceTable
from zombiescan.registry import CHECKS, CheckSpec


@dataclass
class ScanError:
    region: str
    check: str
    message: str


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    attempted: int = 0
    # Pairs where the service has no endpoint in that region at all. A fact
    # about AWS's footprint, not a failure: Lightsail is absent from several
    # regions, and reporting six errors per region for it is pure noise.
    unavailable: int = 0

    @property
    def total_monthly_cost(self) -> float:
        return sum(f.monthly_cost for f in self.findings)

    @property
    def completely_failed(self) -> bool:
        """Every region/check pair errored, so "no findings" means nothing.

        A scan of a region that does not exist reports zero waste and zero
        findings, which is indistinguishable from a clean account unless the
        caller is told the difference.
        """
        return self.attempted > 0 and (len(self.errors) + self.unavailable) == self.attempted


class CredentialError(RuntimeError):
    """Raised when there is no usable session. The message names the fix."""


def resolve_regions(session: boto3.Session, all_regions: bool) -> list[str]:
    """Which regions to scan.

    ``--all-regions`` asks EC2 for the regions this account has enabled;
    opted-out regions are excluded by AWS, so we never waste calls on them.
    """
    if not all_regions:
        region = session.region_name
        if not region:
            raise CredentialError(
                "No region configured. Set one with 'aws configure set region <region>', "
                "pass --region, or use --all-regions."
            )
        return [region]

    client = session.client("ec2", region_name=session.region_name or "us-east-1")
    described = client.describe_regions()["Regions"]
    return sorted(r["RegionName"] for r in described)


def filter_us_regions(regions: list[str]) -> list[str]:
    """Keep only the US regions of ``regions``.

    A prefix test rather than a fixed list, so a US region added after this
    ships is picked up without a code change. It also matches the GovCloud
    names, which a commercial account's describe_regions does not return
    anyway -- they live in another partition.
    """
    kept = [r for r in regions if r.startswith("us-")]
    if not kept:
        raise ValueError(
            f"--us-only leaves nothing to scan: none of {', '.join(regions)} is a US region."
        )
    return kept


def load_packs(disabled: frozenset[str] = frozenset()) -> packs.LoadReport:
    """Import every enabled pack, registering its checks, cleaners and rates.

    Call this before ``select_checks``: until a pack is imported, none of its
    checks exist to be selected.
    """
    return packs.discover(disabled=disabled)


def select_checks(
    names: tuple[str, ...], disabled_packs: frozenset[str] = frozenset()
) -> list[CheckSpec]:
    """The checks to run, honouring --check and --disable-pack.

    ``disabled_packs`` is applied here as well as at import time. Not loading a
    pack is what normally keeps its checks out of the registry, but a pack
    already imported by something else in the process would otherwise slip
    through -- and a flag that silently does nothing is worse than no flag.
    """
    if not names:
        return [spec for spec in CHECKS.values() if spec.pack not in disabled_packs]

    unknown = sorted(set(names) - set(CHECKS))
    if unknown:
        available = ", ".join(sorted(CHECKS))
        raise ValueError(f"unknown check(s): {', '.join(unknown)}. Available: {available}")

    selected = [CHECKS[n] for n in names]
    contradicted = sorted({s.name for s in selected if s.pack in disabled_packs})
    if contradicted:
        # Asking for a check and disabling its pack in the same command is a
        # mistake worth naming, not one to resolve by guessing which the
        # operator meant.
        raise ValueError(
            f"check(s) {', '.join(contradicted)} belong to a pack disabled by "
            "--disable-pack; drop one of the two flags"
        )
    return selected


def verify_credentials(session: boto3.Session) -> str:
    """Return the caller ARN, or raise with a message naming the fix."""
    try:
        return session.client("sts").get_caller_identity()["Arn"]
    except botocore.exceptions.NoCredentialsError as exc:
        raise CredentialError("No AWS credentials found. Run 'aws login' first.") from exc
    except botocore.exceptions.TokenRetrievalError as exc:
        raise CredentialError("AWS session expired or could not refresh. Run 'aws login'.") from exc
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ExpiredToken", "InvalidClientTokenId", "RequestExpired"):
            raise CredentialError("AWS session expired. Run 'aws login'.") from exc
        raise


def _run_one(spec: CheckSpec, session: boto3.Session, region: str, pricing: PriceTable):
    ctx = ScanContext(session=session, region=region, pricing=pricing)
    return list(spec.fn(ctx))


def scan(
    session: boto3.Session,
    regions: list[str],
    checks: list[CheckSpec],
    pricing: PriceTable,
    max_workers: int = 16,
) -> ScanResult:
    # A global check runs once, against whichever region we can reach, rather
    # than once per region -- otherwise one Route 53 health check is reported
    # seventeen times and the waste total is seventeen times too large.
    home = regions[0] if regions else "us-east-1"
    jobs = [(spec, region) for region in regions for spec in checks if not spec.is_global]
    jobs += [(spec, home) for spec in checks if spec.is_global]
    result = ScanResult(regions=list(regions), attempted=len(jobs))
    if not jobs:
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_run_one, spec, session, region, pricing): (spec, region)
            for spec, region in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            spec, region = futures[future]
            try:
                result.findings.extend(future.result())
            except botocore.exceptions.ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                # A region the account cannot reach is not an error worth
                # shouting about -- record it and carry on with the rest.
                if code in ("AuthFailure", "UnauthorizedOperation", "AccessDenied"):
                    result.errors.append(ScanError(region, spec.name, f"{code}: no access"))
                else:
                    result.errors.append(ScanError(region, spec.name, str(exc)))
            except botocore.exceptions.EndpointConnectionError:
                # The service is not offered in this region, so there is
                # nothing to find and nothing went wrong.
                result.unavailable += 1
            except Exception as exc:  # noqa: BLE001 - one bad region must not kill the scan
                result.errors.append(ScanError(region, spec.name, f"{type(exc).__name__}: {exc}"))

    result.findings.sort(key=lambda f: (-f.monthly_cost, f.region, f.resource_id))
    return result
