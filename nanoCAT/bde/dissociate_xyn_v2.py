from collections import abc
from itertools import chain, combinations, groupby, repeat
from typing import (
    Union, Mapping, Iterable, Tuple, Dict, List, Optional, FrozenSet, Generator, Iterator,
    Callable, Any, TypeVar
)

import numpy as np
from scipy.spatial.distance import cdist

from scm.plams import Molecule, Atom, readpdb
from scm.plams.interfaces.molecule import rdkit as molkit
from assertionlib.dataclass import AbstractDataClass

from CAT.mol_utils import to_atnum
from CAT.attachment.ligand_anchoring import _smiles_to_rdmol
from nanoCAT.bde.guess_core_dist import guess_core_core_dist

__all__ = ['MolDissociater']

T = TypeVar('T')
CombinationsTuple = Tuple[List[int], List[int]]


def start_dissociate(mol: Molecule,
                     lig_count: int,
                     lig_pairs: int = 1,
                     core_idx: Union[None, int, Iterable[int]] = None,
                     core_atom: Union[None, int, str] = None,
                     core_smiles: Optional[str] = None,
                     max_core_dist: Optional[float] = None,
                     max_lig_dist: Optional[float] = None,
                     topology: Optional[Mapping[int, str]] = None,
                     **kwargs: Any) -> Generator[Molecule, None, None]:
    r"""Remove :math:`XY_{n}` from **mol** with the help of the :class:`MolDissociater` class.

    The dissociation process consists of 5 general steps:

    * Constructing a :class:`MolDissociater` instance for managing the dissociation workflow.
    * Assigning a topology-descriptor to each atom with :meth:`MolDissociater.assign_topology`.
    * Identifying all valid core/ligand pairs using either :meth:`MolDissociater.get_pairs_closest`
      or :meth:`MolDissociater.get_pairs_distance`.
    * Creating all to-be dissociated core/ligand combinations with
      :meth:`MolDissociater.get_combinations`.
    * Start the dissociation process by calling the earlier
      created :class:`MolDissociater` instance.

    Examples
    --------
    .. code:: python

        >>> from typing import Iterator

        >>> import numpy as np
        >>> from scm.plams import Molecule

        # Define parameters
        >>> mol: Molecule = Molecule(...)
        >>> core_idx: List[int] = [1, 2, 3, 4, 5]
        >>> lig_idx: List[int] = [10, 20, 30, 40]
        >>> lig_count: int = 2

        # Start the workflow
        >>> dissociate = MolDissociater(mol, core_idx, lig_count)
        >>> dissociate.assign_topology()
        >>> pairs: np.ndarray = dissociate.get_pairs_closest(lig_idx)
        >>> combinations: Iterator[tuple] = dissociate.get_combinations(pairs)

        # Create the final iterator
        >>> mol_iterator: Iterator[Molecule] = dissociate(cor_lig_combinations)

    Parameters
    ----------
    mol : |plams.Molecule|
        A molecule.

    lig_count : :class:`int`
        The number of to-be dissociated ligands per core atom/molecule.

    lig_pairs : :class:`int`
        The number of to-be dissociated core/ligand pairs per core atom.
        Core/ligand pairs are picked based on whichever ligands are closest to each core atom.
        This option is irrelevant if a distance based criterium is used (see **max_lig_dist**).

    core_idx : :class:`int` or :class:`Iterable<collections.abc.Iterable>` [:class:`int`], optional
        An index or set of indices with all to-be dissociated core atoms.
        See **core_atom** to define **core_idx** based on a common atomic symbol/number.

    core_atom : :class:`int` or :class:`str`, optional
        An atomic number or symbol used for automatically defining **core_idx**.
        Core atoms within the bulk (rather than on the surface) are ignored.

    core_smiles : :class:`str`, optional
        A SMILES string representing molecule containing **core_idx**.
        Provide a value here if one wants to disociate an entire molecules from the core and
        not just atoms.

    max_core_dist : :class:`float`, optional
        A value representing the mean distance between the core atoms in **core_idx**.
        If ``None``, guess this value based on the radial distribution function of **mol**
        (this is generally recomended).

    max_lig_dist : :class:`float`, optional
        Instead of dissociating a given number of core/ligand pairs (see **lig_pairs**) dissociate
        all pairs within a given distance from a core atom.

    topology : :class:`Mapping<collections.abc.Mapping>` [:class:`int`, :class:`str`], optional
        A mapping neighbouring of atom counts to a user specified topology descriptor
        (*e.g.* ``"edge"``, ``"vertice"`` or ``"face"``).

    \**kwargs : :data:`Any<typing.Any>`
        For catching excess keyword arguments.

    Returns
    -------
    :class:`Generator<collections.abc.Generator>` [|plams.Molecule|]
        A generator yielding new molecules with :math:`XY_{n}` removed.

    """
    if core_atom is None and core_idx is None:
        raise TypeError("The 'core_atom' and 'core_idx' parameters cannot be both 'None'")

    # Set **core_idx** to all atoms within **mol** matching **core_atom**
    if core_atom is not None:
        core_atom = to_atnum(core_atom)
        core_idx = [i for i, at in enumerate(mol, 1) if at.atnum == core_atom]

    # Construct a MolDissociater instance
    dissociate = MolDissociater(mol, core_idx, lig_count, max_core_dist, topology)
    if core_atom:
        dissociate.remove_bulk()
    dissociate.assign_topology()

    # Construct an array with all core/ligand pairs
    lig_idx = (i for i, at in enumerate(mol, 1) if at.properties.anchor)
    if max_lig_dist is not None:
        cor_lig_pairs = dissociate.get_pairs_closest(lig_idx, n_pairs=lig_pairs)
    else:
        cor_lig_pairs = dissociate.get_pairs_distance(lig_idx, max_dist=max_lig_dist)

    # Start with the second residue instead of the first (the first residue is the core)
    cor_lig_pairs[:, 1:] += 1

    # Dissociate the ligands
    cor_lig_combinations = dissociate.get_combinations(cor_lig_pairs, core_smiles=core_smiles)
    return dissociate(cor_lig_combinations)


class MolDissociater(AbstractDataClass):
    """MolDissociater.

    Parameters
    ----------
    mol : |plams.Molecule|
        A PLAMS molecule consisting of cores and ligands.

    core_idx : :class:`int` or :class:`Iterable<colelctions.abc.Iterable>` [:class:`int`]
        An iterable with (1-based) atomic indices of all core atoms valid for dissociation.

    ligand_count : :class:`int`
        The number of ligands to-be dissociation with a single atom from
        :attr:`MolDissociater.core_idx`.

    max_dist : :class:`float`, optional
        Optional: The maximum distance between core atoms for them to-be considered neighbours.
        If ``None``, this value will be guessed based on the radial distribution function of
        **mol**.

    topology : :class:`dict` [:class:`int`, :class:`str`], optional
        A mapping of neighbouring atom counts to a user-specified topology descriptor.

    """

    """####################################### Properties #######################################"""

    @property
    def core_idx(self) -> np.ndarray: return self._core_idx

    @core_idx.setter
    def core_idx(self, value: Union[int, Iterable[int]]) -> None:
        self._core_idx = core_idx = as_array(value, dtype=int, ndmin=1)
        core_idx -= 1
        core_idx.sort()

    @property
    def max_dist(self) -> float: return self._max_dist

    @max_dist.setter
    def max_dist(self, value: Optional[float]) -> None:
        if value is not None:
            self._max_dist = float(value)
        else:
            idx = 1 + int(self.core_idx[0])
            self._max_dist = guess_core_core_dist(self.mol, self.mol[idx])

    @property
    def topology(self) -> Mapping[int, str]: return self._topology

    @topology.setter
    def topology(self, value: Optional[Mapping[int, str]]) -> None:
        self._topology = value if value is not None else {}

    _PRIVATE_ATTR: FrozenSet[str] = frozenset({'_coords', '_partition_dict'})

    def __init__(self, mol: Molecule,
                 core_idx: Union[int, Iterable[int]],
                 ligand_count: int,
                 max_dist: Optional[float] = None,
                 topology: Optional[Mapping[int, str]] = None) -> None:
        """Initialize a :class:`MolDissociater` instance."""
        super().__init__()

        # Public instance variables
        self.mol: Molecule = mol
        self.core_idx: np.ndarray = core_idx
        self.ligand_count: int = ligand_count
        self.max_dist: float = max_dist
        self.topology: Mapping[int, str] = topology

        # Private instance variables
        self._coords: np.ndarray = mol.as_array()
        self._partition_dict: Mapping[int, List[int]] = self.partition_mol()

    @AbstractDataClass.inherit_annotations()
    def _str_iterator(self):
        return ((k.strip('_'), v) for k, v in super()._str_iterator())

    def remove_bulk(self, max_vec_len: float = 0.5) -> None:
        """A function for filtering out atoms specified in :attr:`MolDissociater.core_idx` which are present in the bulk.

        The function searches for all neighbouring core atoms within a radius
        :attr:`MolDissociater.max_dist`.
        Vectors are then constructed from the core atom to the mean positioon of its neighbours.
        Vector lengths close to 0 thus indicate that the core atom is surrounded in a (nearly)
        spherical pattern,
        *i.e.* it's located in the bulk of the material and not on the surface.

        Performs in inplace update of :attr:`MolDissociater.core_idx`.

        Parameters
        ----------
        max_vec_len : :class:`float`
            The maximum length of an atom vector to-be considered part of the bulk.
            Atoms producing smaller values are removed from :attr:`MolDissociater.core_idx`.
            Units are in Angstroem.

        """  # noqa
        xyz: np.ndarray = self._coords
        i: np.ndarray = self.core_idx
        max_dist: float = self.max_dist

        # Construct the distance matrix and fill the diagonal
        dist = cdist(xyz[i], xyz[i])
        np.filldiagonal(dist, max_dist)

        xy = np.array(np.where(dist <= max_dist))
        bincount = np.bincount(xy[0], minlength=len(i))

        # Slice xyz_array, creating arrays of reference atoms and neighbouring atoms
        x = xyz[i]
        y = xyz[i[xy[1]]]

        # Calculate the vector length from each reference atom to the mean position
        # of its neighbours
        # A vector length close to 0.0 implies that a reference atom is surrounded by neighbours in
        # a more or less spherical pattern:
        # i.e. the reference atom is in the bulk and not on the surface
        vec = np.empty((bincount.shape[0], 3), dtype=float)
        start = 0
        for j, step in enumerate(bincount):
            k = slice(start, start+step)
            vec[j] = x[j] - np.average(y[k], axis=0)
            start += step

        vec_norm = np.linalg.norm(vec, axis=1)
        norm_accept, _ = np.where(vec_norm > max_vec_len)
        self.core_idx = i[norm_accept]

    """################################## Topology assignment ##################################"""

    def assign_topology(self) -> None:
        """Assign a topology to all core atoms in :attr:`MolDissociater.core_idx`.

        The topology descriptor is based on:
        * The number of neighbours within a radius defined by :attr:`MolDissociater.max_dist`.
        * The mapping defined in :attr:`MolDissociater.topology`,
          which maps the number of neighbours to a user-defined topology description.

        If no topology description is available for a particular neighbouring atom count,
        then a generic :code:`str(i) + "_neighbours"` descriptor is used
        (where `i` is the neighbouring atom count).

        Performs an inplace update of all |Atom.properties| ``["topology"]`` values.

        """
        # Extract variables
        mol: Molecule = self.mol
        xyz: np.ndarray = self._coords
        i: np.ndarray = self.core_idx
        max_dist: float = self.max_dist

        # Create a distance matrix and find all elements with a distance smaller than **max_dist**
        dist = cdist(xyz[i], xyz[i])
        np.fill_diagonal(dist, max_dist)

        # Find all valid core atoms and create a topology indicator
        valid_core, _ = np.where(dist <= max_dist)
        neighbour_count = np.bincount(valid_core, minlength=len(i))
        neighbour_count -= 1
        topology: List[str] = self._get_topology(neighbour_count)

        for j, top in zip(self.core_idx, topology):
            j = 1 + int(j)  # Switch from 0-based to 1-based indices
            mol[j].properties.topology = top

    def _get_topology(self, neighbour_count: Iterable[int]) -> List[str]:
        """Translate the number of neighbouring atoms (**bincount**) into a list of topologies.

        If a specific number of neighbours (*i*) is absent from **topology_dict** then that
        particular element is set to a generic :code:`str(i) + '_neighbours'`.

        Parameters
        ----------
        neighbour_count : :math:`n` :class:`numpy.ndarray` [:class:`int`]
            An array representing the number of neighbouring atoms per array-element.

        Returns
        -------
        :math:`n` :class:`Iterator` [:class:`str`]
            A list of topologies for all :math:`n` atoms in **bincount**.

        See Also
        --------
        :attr:`MolDissociater.topology`
            A dictionary that maps neighbouring atom counts to a user-specified topology descriptor.

        """
        topology: Mapping[int, str] = self.topology
        return [topology.get(i, f'{i}_neighbours') for i in neighbour_count]

    """############################ core/ligand pair identification ############################"""

    def get_pairs_closest(self, lig_idx: Union[int, Iterable[int]],
                          n_pairs: int = 1) -> np.ndarray:
        """Create and return the indices of each core atom and the :math:`n` closest ligands.

        :math:`n` is defined according to :attr:`MolDissociater.ligand_count`.

        Parameters
        ----------
        lig_idx : :data:`Callable<typing.Callable>`, optional
            The (1-based) indices of all ligand anchor atoms.

        n_pairs : :class:`int`
            The number of to-be returned pairs per core atom.
            If :code:`n_pairs > 1` than each successive set of to-be dissociated ligands is
            determined by the norm of the :math:`n` distances.

        Returns
        -------
        :math:`m*(n+1)` |np.ndarray|_ [|np.int64|_]
            An array with the indices of all :math:`m` valid ligand/core pairs
            and with :code:`n=self.ligand_count`.

        """
        if n_pairs <= 0:
            raise ValueError(f"The 'n_pairs' parameter should be larger than 0")

        # Extract instance variables
        xyz: np.ndarray = self._coords
        i: np.ndarray = self.core_idx
        j: np.ndarray = as_array(lig_idx, dtype=int) - 1
        n: int = self.ligand_count

        # Find all core atoms within a radius **max_dist** from a ligand
        dist = cdist(xyz[i], xyz[j])

        if n_pairs == 1:
            lig_idx = np.argsort(dist, axis=1)[:, :2]
            core_idx = i[:, None]
            return np.hstack([core_idx, lig_idx])

        # Shrink the distance matrix, keep the n_pairs smallest distances per row
        idx_small = np.argsort(dist, axis=1)[:, :1+n_pairs]
        dist_smallest = np.take_along_axis(dist, idx_small, axis=1)

        # Create an array of combinations
        combine = np.fromiter(chain.from_iterable(combinations(range(1+n_pairs), n)), dtype=int)
        combine.shape = -1, n

        # Accept the n_pair entries (per row) based on the norm
        norm = np.linalg.norm(dist_smallest[:, combine], axis=2)
        idx_accept = combine[np.argsort(norm, axis=1)[:, :n_pairs]]

        # Create an array with all core/ligand pairs
        lig_idx = np.array([k[l] for k, l in zip(idx_small, idx_accept)])
        lig_idx.shape = -1, lig_idx.shape[-1]
        core_idx = np.fromiter(iter_repeat(i, n_pairs), dtype=int)[:, None]
        return np.hstack([core_idx, lig_idx])

    def get_pairs_distance(self, lig_idx: Union[int, Iterable[int]],
                           max_dist: float = 5.0) -> np.ndarray:
        """Create and return the indices of each core atom and all ligand pairs with **max_dist**.

        :math:`n` is defined according to :attr:`MolDissociater.ligand_count`.

        Parameters
        ----------
        lig_idx : :data:`Callable<typing.Callable>`, optional
            The (1-based) indices of all ligand anchor atoms.

        max_dist : :class:`float`
            The radius (Angstroem) used as cutoff.

        Returns
        -------
        :math:`m*(n+1)` |np.ndarray|_ [|np.int64|_]
            An array with the indices of all :math:`m` valid ligand/core pairs
            and with :code:`n=self.ligand_count`.

        """
        # Extract instance variables
        xyz: np.ndarray = self._coords
        i: np.ndarray = self.core_idx
        j: np.ndarray = as_array(lig_idx, dtype=int) - 1
        n: int = self.ligand_count

        # Find all core atoms within a radius **max_dist** from a ligand
        dist = cdist(xyz[i], xyz[j])
        np.fill_diagonal(dist, max_dist)

        # Construct a mapping with core atoms and keys and all matching ligands as values
        idx = np.where(dist < max_dist)
        pair_mapping: Dict[int, List[int]] = {
            k: list(v) for k, v in groupby(zip(*idx), key=lambda kv: kv[0])
        }

        # Return a 2D array with all valid core/ligand pairs
        iterator = pair_mapping.items()
        cor_lig_pairs = list(chain.from_iterable(
            ((k,) + n_tup for n_tup in combinations(v, n)) for k, v in iterator if len(v) >= n
        ))

        return np.array(cor_lig_pairs)  # 2D array of integers

    def get_combinations(self, cor_lig_pairs: np.ndarray,
                         core_smiles: Optional[str] = None) -> Iterator[CombinationsTuple]:
        """Create a list with all to-be removed atom combinations.

        Parameters
        ----------
        cor_lig_pairs : :class:`numpy.ndarray`
            An array with the indices of all core/ligand pairs.

        smiles : :class:`str`, optional
            A SMILES string representing a substructure of within the core.
            This entire substructure will be disociated together with ligands
            (rather than just a single core atom).
            See :meth:`MolDissociater._core_neighbours`.

        Returns
        -------
        :class:`list` [:class:`tuple`]
            A list of 2-tuples.
            The first element of each tuple is a list with the (1-based) indices of all
            to-be removed atoms in the core.
            The second element contains an iterator with the (1-based) indices of all to-be removed
            ligands.

        """
        def _get_value(core: Tuple[int, ...],
                       ligands: Iterable[Iterable[int]]) -> CombinationsTuple:
            return core, list(chain.from_iterable(partition_dict[lig]) for lig in ligands)

        # Extract instance variables
        partition_dict: Mapping[int, List[int]] = self._partition_dict

        # Switch from 0-based to 1-based indices
        cor_lig_pairs1 = cor_lig_pairs + 1

        # Fill the to-be returned list
        ligand_list = cor_lig_pairs1[:, 1:].tolist()
        core_iterator = self._core_neighbours(core_smiles)
        return (_get_value(core, ligands) for core, ligands in zip(core_iterator, ligand_list))

    @staticmethod
    def _ValueError(core: np.ndarray, intersect: np.ndarray, smiles: str) -> ValueError:
        """Return a :exc:`ValueError` for :meth:`MolDissociater._core_neighbours`"""
        diff = np.setdiff1d(core, intersect)
        diff.sort()
        return ValueError("A number of atoms specified in the 'core_idx' attribute are "
                          f"absent from '{smiles}';\nabsent atoms: {diff}")

    """################################# Molecule dissociation #################################"""

    def __call__(self, combinations: Iterator[CombinationsTuple]) -> Iterator[Molecule]:
        """Get this party started."""
        # Extract instance variables
        mol: Molecule = self.mol

        # Construct new indices
        indices = self._get_new_indices()

        for core_idx, lig_idx in combinations:
            # Create a new molecule
            mol_new = mol.copy()
            s = mol_new.properties

            # Create a list of to-be removed atoms
            core: Atom = mol_new[core_idx[0]]
            delete_at = [mol_new[i] for i in core_idx]
            delete_at += [mol_new[i] for i in lig_idx]

            # Update the Molecule.properties attribute of the new molecule
            s.indices = indices
            s.job_path = []
            s.core_topology = f'{core.properties.topology}_{core_idx[0]}'
            s.lig_residue = sorted(
                [mol_new[i[0]].properties.pdb_info.ResidueNumber for i in lig_idx]
            )
            s.df_index: str = s.core_topology + ' '.join(str(i) for i in s.lig_residue)

            for at in delete_at:
                mol_new.delete_atom(at)
            yield mol_new

    def _get_new_indices(self) -> List[int]:
        """Return an updated version of :attr:`MolDissociater.mol` ``.properties.indices``."""
        n: int = self.ligand_count
        mol: Molecule = self.mol
        partition_dict: Mapping[int, List[Atom]] = self._partition_dict

        if not mol.properties.indices:
            mol.properties.indices = indices = []
            return indices

        # Delete the indices of the last n ligands
        ret = mol.properties.indices.copy()
        for _ in range(n):
            del ret[-1]

        # Delete the index of the last core atom
        core_max = next(partition_dict.values())[-1]
        idx = ret.index(core_max)
        del ret[idx]

        # Update the indices of all remaining ligands
        for i in ret:
            i -= 1
        return ret

    """########################################## Misc ##########################################"""

    def partition_mol(self, key: Optional[Callable[[Atom], int]] = None
                      ) -> Dict[int, List[int]]:
        """Partition the atoms within a molecule based on a user-specified criteria.

        Parameters
        ----------
        key : :data:`Callable<typing.Callable>`, optional
            A callable which takes an Atom as argument and returns an integer.
            If ``None``, use the |Atom.properties| ``["pdb_info"]["ResidueNumber"]`` attribute.

        Returns
        -------
        :class:`dict` [:class:`int`, :class:`list` [:class:`int`]]
            A dictionary with keys construcetd by **key** and values consisting of
            lists with matching atomic indices.

        """
        func = key if key is not None else lambda at: at.properties.pdb_info.ResidueNumber

        list_append = {}
        ret = {}
        for i, at in enumerate(self.mol, 1):
            key = func(at)
            try:
                list_append[key](i)
            except KeyError:
                ret[key] = [i]
                list_append[key] = ret[key].append
        return ret

    def _core_neighbours(self, smiles: Optional[str] = None) -> Iterator[Tuple[int, ...]]:
        """Gather all neighbouring core atoms based on a substructure defined by **smiles**.

        Parameter
        ---------
        smiles : :class:`str`, optional
            A SMILES string representing a substructure of within the core.
            This entire substructure will be disociated together with ligands
            (rather than just a single core atom).

        Returns
        -------
        :class:`Iterator<collections.abc.Iterator>` [:class:`tuple` [:class:`int`]]
            An iterator returning tuples with (1-based) atomic indices.
            If :code:`smiles=None` then just return the each element from **core** in a tuple.

        Raises
        ------
        ValueError
            Raised if 1 or more core atoms (specified in :attr:`MolDissociater.core_idx`) are not
            part of the user-specified substructure.

        """
        core = self.core_idx
        if smiles is None:
            return iter((1+core).tolist())

        # Find substructure matches
        mol = self.mol
        rdmol = molkit.to_rdmol(mol)
        rd_smiles = _smiles_to_rdmol(smiles)
        matches = np.array(rdmol.GetSubstructMatches(rd_smiles, useChirality=True))

        # Ensure that all user-specified core atoms are part of the substructure matches
        intersect, _, _idx = np.intersect1d(core, matches)
        if (intersect != core).all():  # Uhoh
            with np.printoptions(threshold=0, edgeitems=3):
                raise self._ValueError(core, intersect, smiles)

        # Return only the matches which contain a user-specified core atom
        _idx //= matches.shape[1]
        idx = matches[_idx]
        idx += 1
        return iter(idx.tolist())


def mark_substructure(mol: Molecule, smiles: str) -> None:
    atoms = mol.atoms
    rdmol = molkit.to_rdmol(mol)
    rd_smiles = _smiles_to_rdmol(smiles)

    matches: Iterable[Tuple[int, ...]] = rdmol.GetSubstructMatches(rd_smiles, useChirality=True)
    for i, tup in enumerate(matches):
        for j in tup:
            atoms[j].properties.smiles_partition = i


def iter_repeat(iterable: Iterable[T], times: int) -> Iterator[T]:
    """Iterate over an iterable and apply :func:`itertools.repeat` to each element.

    Examples
    --------
    .. code:: python

        >>> iterable = range(3)
        >>> times = 2
        >>> iterator = iter_repeat(iterable, n)
        >>> for i in iterator:
        ...     print(i)
        0
        0
        1
        1
        2
        2

    Parameters
    --------
    iterable : :class:`Iterable<collections.abc.Iterable>`
        An iterable.

    times : :class:`int`
        The number of times each element should be repeated.

    Returns
    -------
    :class:`Iterator<collections.abc.Iterator>`
        An iterator that yields each element from **iterable** multiple **times**.

    """
    return chain.from_iterable(repeat(i, times) for i in iterable)


def as_array(iterable: Iterable, dtype: Union[None, type, np.dtype] = None,
             copy: bool = True, ndmin: Union[int, Tuple[int, ...]] = 0) -> np.ndarray:
    """Convert a generic iterable (including iterators) into a NumPy array.

    See :func:`numpy.array` for an extensive description of all parameters.

    """
    if isinstance(iterable, abc.Iterator):
        ret = np.fromiter(iterable, dtype=dtype)
    else:
        ret = np.array(iterable, dtype=dtype, copy=copy)
    if ret.ndim < ndmin:
        ret.shape += (1,) * (ndmin - ret.ndmin)
    return ret


file = '/Users/bvanbeek/Documents/CdSe/Week_5/qd/Cd68Cl26Se55__26_C#CCCC[=O][O-]@O7.pdb'
mol: Molecule = readpdb(file)
for at in mol:
    if at.properties.charge == -1:
        at.properties.anchor = True

core_idx = (i for i, at in enumerate(mol, 1) if at.symbol == 'Cd')
lig_count = 2
dissociater = MolDissociater(mol, core_idx, lig_count)
