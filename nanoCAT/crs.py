"""
nanoCAT.crs
===========

A module designed for running COSMO-RS Jobs.

Index
-----
.. currentmodule:: nanoCAT.crs
.. autosummary::
    CRSResults
    CRSJob

API
---
.. autoclass:: nanoCAT.crs.CRSResults
.. autoclass:: nanoCAT.crs.CRSJob

"""

import numpy as np

try:
    import pandas as pd
    PANDAS = True
except ImportError:
    PANDAS = False

from scm.plams.core.basejob import SingleJob
from scm.plams.tools.units import Units
from scm.plams.interfaces.adfsuite.scmjob import (SCMJob, SCMResults)

__all__ = ['CRSResults', 'CRSJob']


class CRSResults(SCMResults):
    """A class for accessing results of COSMO-RS jobs."""
    _kfext = '.crskf'
    _rename_map = {'CRSKF': '$JN.crskf'}

    def get_energy(self, unit: str = 'kcal/mol') -> float:
        """Returns the solute solvation energy from an Activity Coefficients calculation."""
        E = self.readkf('ACTIVITYCOEF', 'deltag')[0]
        return Units.convert(E, 'kcal/mol', unit)

    def get_activity_coefficient(self) -> float:
        """Returns the solute activity coefficient from an Activity Coefficients calculation."""
        return self.readkf('ACTIVITYCOEF', 'gamma')[0]

    def get_sigma_profile(self, unit: str = 'kcal/mol'):
        """ Returns all sigma profiles, expressed in *unit*.
        Returns a dictionary of numpy arrays or, if available, a pandas dataframe. """
        return self.get_sigma('SIGMAPOTENTIAL', unit)

    def get_sigma_potential(self):
        """Returns all sigma potentials.

        Returns a dictionary of numpy arrays or, if available, a pandas dataframe."""
        return self.get_sigma('SIGMAPROFILE')

    def get_sigma(self, section: str, unit: str = 'kcal/mol'):
        """Grab all values of sigma and the sigmapotential/profile;
        combine them into a dictionary or pandas dataframe. """
        sigma = self._sigma_y(section, unit)
        if self.readkf('PURE' + section) is not None:
            sigma['mixture'] = self._sigma_y(section, unit)
        sigma['sigma'] = self._sigma_x(section)
        try:
            return sigma.set_index('sigma')
        except AttributeError:
            return sigma

    def _sigma_x(self, section: str) -> np.ndarray:
        """Get all values of sigma."""
        min_max = self.readkf(section, 'sigmax')
        nitems = self.readkf(section, 'nitems')
        step = int((1 + 2 * min_max) / nitems)
        return np.arange(-min_max, min_max, step)

    def _sigma_y(self, section: str, unit: str = 'kcal/mol'):
        """Get all values of."""
        values = np.array(self.readkf(section, 'profil'))
        values *= Units.conversion_ratio('kcal/mol', unit)
        if 'PURE' in section:
            ncomp = self.readkf(section, 'ncomp')
            values.shape = len(values) // ncomp, ncomp
        keys = self.readkf(section, 'filename')
        ret = dict(zip(keys, values))
        try:
            return pd.DataFrame(ret).set_index('sigma')
        except NameError:
            return ret


class CRSJob(SCMJob):
    """A class for running COSMO-RS jobs."""
    _command = 'crs'
    _result_type = CRSResults

    def __init__(self, **kwargs) -> None:
        SingleJob.__init__(self, **kwargs)
        self.ignore_molecule = True
