"""Importing this package registers every check."""

from zombiescan.checks import (
    available_eni,  # noqa: F401
    classic_load_balancer,  # noqa: F401
    detached_internet_gateway,  # noqa: F401
    disabled_kms_key,  # noqa: F401
    empty_vpc,  # noqa: F401
    idle_load_balancer,  # noqa: F401
    idle_nat_gateway,  # noqa: F401
    idle_provisioned_dynamodb,  # noqa: F401
    incomplete_multipart_upload,  # noqa: F401
    log_group_no_retention,  # noqa: F401
    orphaned_rds_snapshot,  # noqa: F401
    orphaned_snapshots,  # noqa: F401
    stale_secret,  # noqa: F401
    stopped_instances,  # noqa: F401
    stopped_rds_instance,  # noqa: F401
    unassociated_eip,  # noqa: F401
    unattached_ebs,  # noqa: F401
    unmounted_efs,  # noqa: F401
    unused_ami,  # noqa: F401
    unused_route53_health_check,  # noqa: F401
    unused_security_group,  # noqa: F401
    unused_vpc_endpoint,  # noqa: F401
)
