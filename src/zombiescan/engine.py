"""Region fan-out and check execution.

Every AWS call made from here is read-only. The engine never mutates anything
and never runs generated remediation.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field

import boto3
import botocore.exceptions

import zombiescan.checks  # noqa: F401  (registers the checks)
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
        return self.attempted > 0 and len(self.errors) == self.attempted


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


def select_checks(names: tuple[str, ...]) -> list[CheckSpec]:
    if not names:
        return list(CHECKS.values())
    unknown = sorted(set(names) - set(CHECKS))
    if unknown:
        available = ", ".join(sorted(CHECKS))
        raise ValueError(f"unknown check(s): {', '.join(unknown)}. Available: {available}")
    return [CHECKS[n] for n in names]


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
    jobs = [(spec, region) for region in regions for spec in checks]
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
            except Exception as exc:  # noqa: BLE001 - one bad region must not kill the scan
                result.errors.append(ScanError(region, spec.name, f"{type(exc).__name__}: {exc}"))

    result.findings.sort(key=lambda f: (-f.monthly_cost, f.region, f.resource_id))
    return result
