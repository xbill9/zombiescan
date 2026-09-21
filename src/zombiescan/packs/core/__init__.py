"""The core pack: the checks that ship with zombiescan itself.

Nothing here is privileged. It loads through the same discovery path as a pack
installed from PyPI, which is the point -- if the pack seam broke, every check
in this directory would stop working and the test suite would say so.
"""

from zombiescan import __version__
from zombiescan.packs import import_pack_modules, register_pack

register_pack(
    "core",
    version=__version__,
    description="Unattached, idle and orphaned resources across EC2, RDS, S3, "
    "CloudWatch, KMS, ECR and friends.",
    homepage="https://github.com/xbill9/zombiescan",
)

import_pack_modules(__name__, __path__)
