"""
CAT.recipes
===========

A number of recipes constructed using the CAT and Nano-CAT packages.

Examples
--------
.. code:: python

    >>> from nanoCAT.recipes import bulk_workflow
    >>> from nanoCAT.recipes import get_lig_charge
    >>> from nanoCAT.recipes import replace_surface
    >>> from nanoCAT.recipes import coordination_number
    >>> from nanoCAT.recipes import dissociate_surface, row_accumulator
    ...

"""

from .bulk import bulk_workflow
from .charges import get_lig_charge
from .mark_surface import replace_surface
from .surface_dissociation import dissociate_surface, row_accumulator
from .coordination_number import coordination_number

__all__ = [
    'bulk_workflow', 'replace_surface', 'dissociate_surface',
    'row_accumulator', 'get_lig_charge', 'coordination_number'
]
