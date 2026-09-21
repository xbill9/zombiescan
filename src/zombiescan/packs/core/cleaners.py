"""Cleaners for the core pack's checks.

Cleaners plan; they never execute. See ``zombiescan.cleaners`` for the split
between planning and running, and ``zombiescan.clean`` for the runner.
"""

from __future__ import annotations

from collections.abc import Iterator

from zombiescan.cleaners import Step, _stamp, cleaner
from zombiescan.models import Finding, ScanContext

# --- storage -------------------------------------------------------------


@cleaner("unattached-ebs")
def clean_unattached_ebs(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    volume = finding.resource_id
    yield Step(
        f"snapshot {volume} before deleting it",
        "ec2",
        "create_snapshot",
        {"VolumeId": volume, "Description": f"zombiescan pre-delete {_stamp()}"},
    )
    yield Step(f"delete volume {volume}", "ec2", "delete_volume", {"VolumeId": volume})


@cleaner("orphaned-snapshot")
def clean_orphaned_snapshot(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete snapshot {finding.resource_id}",
        "ec2",
        "delete_snapshot",
        {"SnapshotId": finding.resource_id},
        irreversible=True,
    )


@cleaner("unused-ami")
def clean_unused_ami(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    image = finding.resource_id
    yield Step(
        f"deregister AMI {image}",
        "ec2",
        "deregister_image",
        {"ImageId": image},
        irreversible=True,
    )
    # Deregistering leaves the snapshots behind, which is the whole reason this
    # finding exists. Deleting them is the point, not a bonus.
    for snapshot in finding.details.get("snapshot_ids", []):
        yield Step(
            f"delete snapshot {snapshot} behind {image}",
            "ec2",
            "delete_snapshot",
            {"SnapshotId": snapshot},
            irreversible=True,
        )


@cleaner("unmounted-efs")
def clean_unmounted_efs(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete file system {finding.resource_id} and everything in it",
        "efs",
        "delete_file_system",
        {"FileSystemId": finding.resource_id},
        irreversible=True,
    )


@cleaner("incomplete-multipart-upload")
def clean_incomplete_multipart_upload(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    bucket = finding.resource_id
    client = ctx.client("s3")
    # Re-list at plan time: the finding records only the first few keys, and
    # the set may have moved on since the scan.
    for page in client.get_paginator("list_multipart_uploads").paginate(Bucket=bucket):
        for upload in page.get("Uploads", []):
            key, upload_id = upload.get("Key"), upload.get("UploadId")
            if not key or not upload_id:
                continue
            yield Step(
                f"abort upload of {key} in {bucket}",
                "s3",
                "abort_multipart_upload",
                {"Bucket": bucket, "Key": key, "UploadId": upload_id},
                irreversible=True,
            )


# --- compute and network -------------------------------------------------


@cleaner("stopped-instance")
def clean_stopped_instance(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    instance = finding.resource_id
    yield Step(
        f"image {instance} before terminating it",
        "ec2",
        "create_image",
        {"InstanceId": instance, "Name": f"zombiescan-{instance}-{_stamp()}"},
    )
    yield Step(
        f"terminate {instance}",
        "ec2",
        "terminate_instances",
        {"InstanceIds": [instance]},
        irreversible=True,
    )


@cleaner("unassociated-eip")
def clean_unassociated_eip(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    public_ip = finding.details.get("public_ip", finding.resource_id)
    # EC2-Classic addresses have no allocation id and are released by IP.
    params = (
        {"AllocationId": finding.resource_id}
        if finding.resource_id.startswith("eipalloc-")
        else {"PublicIp": public_ip}
    )
    yield Step(
        f"release Elastic IP {public_ip}", "ec2", "release_address", params, irreversible=True
    )


@cleaner("available-eni")
def clean_available_eni(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete network interface {finding.resource_id}",
        "ec2",
        "delete_network_interface",
        {"NetworkInterfaceId": finding.resource_id},
    )


@cleaner("idle-nat-gateway")
def clean_idle_nat_gateway(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete NAT gateway {finding.resource_id}",
        "ec2",
        "delete_nat_gateway",
        {"NatGatewayId": finding.resource_id},
    )


@cleaner("unused-vpc-endpoint")
def clean_unused_vpc_endpoint(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete VPC endpoint {finding.resource_id}",
        "ec2",
        "delete_vpc_endpoints",
        {"VpcEndpointIds": [finding.resource_id]},
    )


@cleaner("unused-security-group")
def clean_unused_security_group(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete security group {finding.resource_id}",
        "ec2",
        "delete_security_group",
        {"GroupId": finding.resource_id},
    )


@cleaner("detached-internet-gateway")
def clean_detached_internet_gateway(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete internet gateway {finding.resource_id}",
        "ec2",
        "delete_internet_gateway",
        {"InternetGatewayId": finding.resource_id},
    )


@cleaner("idle-load-balancer")
def clean_idle_load_balancer(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    arn = finding.details.get("arn")
    if not arn:
        return
    yield Step(
        f"delete load balancer {finding.resource_id}",
        "elbv2",
        "delete_load_balancer",
        {"LoadBalancerArn": arn},
    )


@cleaner("empty-classic-lb")
def clean_empty_classic_lb(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete classic load balancer {finding.resource_id}",
        "elb",
        "delete_load_balancer",
        {"LoadBalancerName": finding.resource_id},
    )


# --- data services -------------------------------------------------------


@cleaner("stopped-rds-instance")
def clean_stopped_rds_instance(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    identifier = finding.resource_id
    yield Step(
        f"delete database {identifier}, taking a final snapshot",
        "rds",
        "delete_db_instance",
        {
            "DBInstanceIdentifier": identifier,
            "FinalDBSnapshotIdentifier": f"{identifier}-zombiescan-{_stamp()}",
            "SkipFinalSnapshot": False,
            "DeleteAutomatedBackups": False,
        },
        irreversible=True,
    )


@cleaner("orphaned-rds-snapshot")
def clean_orphaned_rds_snapshot(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete snapshot {finding.resource_id}",
        "rds",
        "delete_db_snapshot",
        {"DBSnapshotIdentifier": finding.resource_id},
        irreversible=True,
    )


@cleaner("idle-provisioned-dynamodb")
def clean_idle_provisioned_dynamodb(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete table {finding.resource_id}",
        "dynamodb",
        "delete_table",
        {"TableName": finding.resource_id},
        irreversible=True,
    )


@cleaner("log-group-no-retention")
def clean_log_group_no_retention(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    days = finding.details.get("suggested_retention_days", 30)
    # Not a deletion, but it destroys every event older than the window the
    # moment it is applied. That is the point, and it is not undoable.
    yield Step(
        f"set {days}-day retention on {finding.resource_id}, deleting older events",
        "logs",
        "put_retention_policy",
        {"logGroupName": finding.resource_id, "retentionInDays": days},
        irreversible=True,
    )


@cleaner("disabled-kms-key")
def clean_disabled_kms_key(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"schedule key {finding.resource_id} for deletion in 30 days",
        "kms",
        "schedule_key_deletion",
        {"KeyId": finding.resource_id, "PendingWindowInDays": 30},
        irreversible=True,
    )


@cleaner("stale-secret")
def clean_stale_secret(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete secret {finding.resource_id} with a 30-day recovery window",
        "secretsmanager",
        "delete_secret",
        {"SecretId": finding.resource_id, "RecoveryWindowInDays": 30},
    )


@cleaner("unused-route53-health-check")
def clean_unused_route53_health_check(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        f"delete health check {finding.resource_id}",
        "route53",
        "delete_health_check",
        {"HealthCheckId": finding.resource_id},
    )


@cleaner("unused-route53-zone")
def clean_unused_route53_zone(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    # Nothing to back up: the only records in a zone this check flags are the
    # SOA and NS sets Route 53 generates, and a recreated zone generates its
    # own. Irreversible all the same -- a public zone's name servers go with
    # it, so a domain delegated to them has to be repointed at whatever a new
    # zone is given.
    name = finding.details.get("name", "")
    namespace = finding.details.get("cloud_map_namespace")
    if namespace:
        # Deleting the zone under a Cloud Map namespace orphans the namespace.
        # Deleting the namespace takes the zone with it, in the namespace's own
        # region rather than the operator's.
        yield Step(
            f"delete Cloud Map namespace {namespace} ({name}), which owns this zone".strip(),
            "servicediscovery",
            "delete_namespace",
            {"Id": namespace},
            irreversible=True,
            region=finding.details.get("cloud_map_region"),
        )
        return

    yield Step(
        f"delete hosted zone {finding.resource_id} ({name})".strip(),
        "route53",
        "delete_hosted_zone",
        {"Id": finding.resource_id},
        irreversible=True,
    )


# `empty-vpc` has no cleaner on purpose. Deleting a VPC fails until every
# subnet, route table, gateway and peering connection inside it is gone, and
# working out that order safely is a different tool. The finding stays
# informational.
