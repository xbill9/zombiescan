"""Importing this package registers every check."""

from zombiescan.checks import (
    available_eni,  # noqa: F401
    idle_nat_gateway,  # noqa: F401
    orphaned_snapshots,  # noqa: F401
    stopped_instances,  # noqa: F401
    unassociated_eip,  # noqa: F401
    unattached_ebs,  # noqa: F401
)
