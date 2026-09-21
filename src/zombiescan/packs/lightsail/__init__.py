"""The Lightsail pack.

Lightsail is a separate pack rather than six more modules in ``core`` because
it is genuinely separate: it has its own API, its own pricing source
(``GetBundles``, not the Price List), and its own idea of how long a month is
(744 hours, not 730). Keeping it apart proves the pack seam carries everything
a pack needs -- checks, cleaners, rates and a refresher -- rather than only the
easy part.
"""

from zombiescan import __version__
from zombiescan.packs import import_pack_modules, register_pack

register_pack(
    "lightsail",
    version=__version__,
    description="Stopped instances, unattached disks and static IPs, idle "
    "container services and orphaned snapshots in Lightsail.",
    homepage="https://github.com/xbill9/zombiescan",
)

from zombiescan.packs.lightsail import rates  # noqa: E402,F401  (registers Lightsail's rates)

import_pack_modules(__name__, __path__)
