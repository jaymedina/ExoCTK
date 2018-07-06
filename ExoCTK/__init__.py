"""
The Exoplanet Characterization Tool Kit is a collection of packages used to reduce and analyze observations of transiting exoplanets
"""
# Licensed under a 3-clause BSD style license - see LICENSE.rst

# Packages may add whatever they like to this file, but
# should keep this content at the top.
# ----------------------------------------------------------------------------
from ._astropy_init import *
# ----------------------------------------------------------------------------

# Enforce Python version check during package import.
# This is the same check as the one at the top of setup.py
import sys

__minimum_python_version__ = "3.5"

class UnsupportedPythonError(Exception):
    pass

if sys.version_info < tuple((int(val) for val in __minimum_python_version__.split('.'))):
    raise UnsupportedPythonError("core does not support Python < {}".format(__minimum_python_version__))

# For egg_info test builds to pass, put package imports here.
if not _ASTROPY_SETUP_:
    from .core import *
    from .svo import *
    from . import contam_visibility
    from . import integrations_groups
    from . import limb_darkening
    from . import nircam_coronagraphy
    from . import lightcurve_fitting
