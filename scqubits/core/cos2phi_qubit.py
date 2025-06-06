# cos2phi_qubit.py
#
# This file is part of scqubits: a Python package for superconducting qubits,
# Quantum 5, 583 (2021). https://quantum-journal.org/papers/q-2021-11-17-583/
#
#    Copyright (c) 2019, Jens Koch and Peter Groszkowski
#    All rights reserved.
#
#    This source code is licensed under the BSD-style license found in the
#    LICENSE file in the root directory of this source tree.
############################################################################

import math

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import scipy as sp

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from numpy import ndarray
from scipy import sparse
from scipy.sparse import coo_matrix, csc_matrix, dia_matrix

import scqubits.core.constants as constants
import scqubits.core.descriptors as descriptors
import scqubits.core.discretization as discretization
import scqubits.core.operators as op
import scqubits.core.oscillator as osc
import scqubits.core.qubit_base as base
import scqubits.core.storage as storage
import scqubits.core.units as units
import scqubits.io_utils.fileio_serializers as serializers
import scqubits.utils.plotting as plot
import scqubits.utils.spectrum_utils as utils

from scqubits.core.noise import NOISE_PARAMS, NoisySystem, calc_therm_ratio
from scqubits.core.storage import WaveFunctionOnGrid


# - Cosine-2-phi qubit noise class ------------------------------------------
class NoisyCos2PhiQubit(NoisySystem, ABC):
    """Abstract base class for incorporating noise in the Cos2PhiQubit.

    This class defines the interface for noise-related operators and calculations
    specific to this qubit type. It provides methods to calculate decoherence
    times (T1) due to various noise channels like inductive dissipation,
    capacitive dissipation, and Purcell effect.

    Subclasses must implement the required operators (phi_1, phi_2, n_1, n_2, n_zeta)
    that are used to calculate the noise effects.
    """

    @abstractmethod
    def phi_1_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        """Returns the operator representing the phase across inductor 1.

        This operator corresponds to the phase difference across one of the
        effective inductors in the circuit, typically defined as φ_1 = ζ - φ.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the native basis.
            If `True`, returns operator in the energy eigenbasis.
            If a tuple `(evals, evecs)`, returns operator in the basis provided by `evecs`.

        Returns
        -------
            The φ_1 operator in the specified basis.
        """
        pass

    @abstractmethod
    def phi_2_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        """Returns the operator representing the phase across inductor 2.

        This operator corresponds to the phase difference across the other
        effective inductor in the circuit, typically defined as φ_2 = -ζ - φ.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the native basis.
            If `True`, returns operator in the energy eigenbasis.
            If a tuple `(evals, evecs)`, returns operator in the basis provided by `evecs`.

        Returns
        -------
            The φ_2 operator in the specified basis.
        """
        pass

    @abstractmethod
    def n_1_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        """Returns the operator representing the charge difference across junction 1.

        This operator corresponds to the charge variable conjugate to the phase
        across junction 1, typically defined as n_1 = (n_φ + n_θ - n_ζ)/2.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the native basis.
            If `True`, returns operator in the energy eigenbasis.
            If a tuple `(evals, evecs)`, returns operator in the basis provided by `evecs`.

        Returns
        -------
            The n_1 operator in the specified basis.
        """
        pass

    @abstractmethod
    def n_2_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        """Returns the operator representing the charge difference across junction 2.

        This operator corresponds to the charge variable conjugate to the phase
        across junction 2, typically defined as n_2 = (n_φ - n_θ + n_ζ)/2.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the native basis.
            If `True`, returns operator in the energy eigenbasis.
            If a tuple `(evals, evecs)`, returns operator in the basis provided by `evecs`.

        Returns
        -------
            The n_2 operator in the specified basis.
        """
        pass

    @abstractmethod
    def n_zeta_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        """Returns the operator representing the charge variable n_ζ.

        This operator corresponds to the charge variable conjugate to the ζ
        degree of freedom, which is related to the shunt capacitor.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the native basis.
            If `True`, returns operator in the energy eigenbasis.
            If a tuple `(evals, evecs)`, returns operator in the basis provided by `evecs`.

        Returns
        -------
            The n_ζ operator in the specified basis.
        """
        pass

    def t1_inductive(
        self,
        i: int = 1,
        j: int = 0,
        Q_ind: Union[float, Callable] = None,
        T: float = NOISE_PARAMS["T"],
        total: bool = True,
        esys: Tuple[ndarray, ndarray] = None,
        get_rate: bool = False,
    ) -> float:
        r"""Calculates T₁ relaxation time/rate due to inductive dissipation in superinductors.

        This noise channel considers energy dissipation due to losses in the inductors
        L₁ and L₂ (with potential disorder dL). The quality factor Q_ind characterizes
        these losses.

        The calculation involves two contributions, one for each inductor, using
        their respective phase operators φ₁ and φ₂.

        If Q_ind is not provided, a default frequency-dependent quality factor
        based on Smith et al. (2020) is used.

        References: Nguyen et al. (2019) [Phys. Rev. Applied 11, 044072],
                    Smith et al. (2020) [NPJ Quantum Inf. 6, 8]

        Parameters
        ----------
        i: int >=0
            Initial state index for the transition i→j
        j: int >=0
            Final state index for the transition i→j
        Q_ind:
            Inductive quality factor; can be a fixed value, a function of frequency ω,
            or None to use a default model
        T:
            Temperature in Kelvin
        total:
            If True (default), return the total rate/time considering transitions i→j and j→i
            If False, consider only the i→j transition
        esys:
            Optional tuple (eigenvalues, eigenvectors) to use for calculations
        get_rate:
            If True, return the relaxation rate Γ₁ (inverse of T₁)
            If False (default), return the relaxation time T₁

        Returns
        -------
        float
            Decoherence time T₁ in units of 2π (system units), or rate Γ₁ in inverse units

        Raises
        ------
        RuntimeError
            If 't1_inductive' is not in self.supported_noise_channels()
        """
        if "t1_inductive" not in self.supported_noise_channels():
            raise RuntimeError(
                "Noise channel 't1_inductive' is not supported in this system."
            )

        if Q_ind is None:
            # See Smith et al (2020)
            def q_ind_fun(omega):
                therm_ratio = abs(calc_therm_ratio(omega, T))
                therm_ratio_500MHz = calc_therm_ratio(
                    2 * np.pi * 500e6, T, omega_in_standard_units=True
                )
                return (
                    500e6
                    * (
                        sp.special.kv(0, 1 / 2 * therm_ratio_500MHz)
                        * np.sinh(1 / 2 * therm_ratio_500MHz)
                    )
                    / (
                        sp.special.kv(0, 1 / 2 * therm_ratio)
                        * np.sinh(1 / 2 * therm_ratio)
                    )
                )

        elif callable(Q_ind):  # Q_ind is a function of omega
            q_ind_fun = Q_ind
        else:  # Q_ind is given as a number

            def q_ind_fun(omega):
                return Q_ind

        def spectral_density1(omega, T):
            r"""Calculates the first spectral density from the angular frequency and
            temperature.

            Parameters
            ----------
            omega:
                angular frequency in system units (units of frequency)
            T:
                temperature in Kelvin

            Returns
            -------
            Spectral density
            """
            therm_ratio = calc_therm_ratio(omega, T)
            s = (
                2
                * self.EL
                / (1 - self.dL)
                / q_ind_fun(omega)
                * (1 / np.tanh(0.5 * np.abs(therm_ratio)))
                / (1 + np.exp(-therm_ratio))
            )
            s *= (
                2 * np.pi
            )  # We assume that system energies are given in units of frequency
            return s

        noise_op1 = self.phi_1_operator()

        def spectral_density2(omega, T):
            r"""Calculates the second spectral density from the angular frequency and
            temperature.

            Parameters
            ----------
            omega:
                angular frequency in system units (units of frequency)
            T:
                temperature in Kelvin

            Returns
            -------
            Spectral Density
            """
            therm_ratio = calc_therm_ratio(omega, T)
            s = (
                2
                * self.EL
                / (1 + self.dL)
                / q_ind_fun(omega)
                * (1 / np.tanh(0.5 * np.abs(therm_ratio)))
                / (1 + np.exp(-therm_ratio))
            )
            s *= (
                2 * np.pi
            )  # We assume that system energies are given in units of frequency
            return s

        noise_op2 = self.phi_2_operator()

        rate_1 = self.t1(
            i=i,
            j=j,
            noise_op=noise_op1,
            spectral_density=spectral_density1,
            total=total,
            esys=esys,
            get_rate=True,
        )
        rate_2 = self.t1(
            i=i,
            j=j,
            noise_op=noise_op2,
            spectral_density=spectral_density2,
            total=total,
            esys=esys,
            get_rate=True,
        )

        if get_rate:
            return rate_1 + rate_2
        else:
            return 1 / (rate_1 + rate_2)

    def t1_capacitive(
        self,
        i: int = 1,
        j: int = 0,
        Q_cap: Union[float, Callable] = None,
        T: float = NOISE_PARAMS["T"],
        total: bool = True,
        esys: Tuple[ndarray, ndarray] = None,
        get_rate: bool = False,
    ) -> float:
        r""":math:`T_1` due to dielectric dissipation in the Josephson junction
        capacitances.

        References:  Nguyen et al (2019), Smith et al (2020)

        Parameters
        ----------
        i: int >=0
            state index that along with j defines a transition (i->j)
        j: int >=0
            state index that along with i defines a transition (i->j)
        Q_cap
            capacitive quality factor; a fixed value or function of `omega`
        T:
            temperature in Kelvin
        total:
            if False return a time/rate associated with a transition from state i to state j.
            if True return a time/rate associated with both i to j and j to i transitions
        esys:
            evals, evecs tuple
        get_rate:
            get rate or time

        Returns
        -------
        time or rate: float
            decoherence time in units of :math:`2\pi` (system units), or rate
             in inverse units.
        """
        if "t1_capacitive" not in self.supported_noise_channels():
            raise RuntimeError(
                "Noise channel 't1_capacitive' is not supported in this system."
            )

        if Q_cap is None:
            # See Smith et al (2020)
            def q_cap_fun(omega):
                return (
                    1e6
                    * (2 * np.pi * 6e9 / np.abs(units.to_standard_units(omega))) ** 0.7
                )

        elif callable(Q_cap):  # Q_cap is a function of omega
            q_cap_fun = Q_cap
        else:  # Q_cap is given as a number

            def q_cap_fun(omega):
                return Q_cap

        def spectral_density1(omega, T):
            r"""Calculates the first spectral density from the angular frequency and
            temperature.

            Parameters
            ----------
            omega:
                angular frequency in system units (units of frequency)
            T:
                temperature in Kelvin

            Returns
            -------
            Spectral density
            """
            therm_ratio = calc_therm_ratio(omega, T)
            s1 = (
                2
                * 8
                * self.ECJ
                / (1 - self.dCJ)
                / q_cap_fun(omega)
                * (1 / np.tanh(0.5 * np.abs(therm_ratio)))
                / (1 + np.exp(-therm_ratio))
            )
            s1 *= (
                2 * np.pi
            )  # We assume that system energies are given in units of frequency
            return s1

        def spectral_density2(omega, T):
            r"""Calculates the second spectral density from the angular frequency and
            temperature.

            Parameters
            ----------
            omega:
                angular frequency in system units (units of frequency)
            T:
                temperature in Kelvin

            Returns
            -------
            Spectral density
            """
            therm_ratio = calc_therm_ratio(omega, T)
            s2 = (
                2
                * 8
                * self.ECJ
                / (1 + self.dCJ)
                / q_cap_fun(omega)
                * (1 / np.tanh(0.5 * np.abs(therm_ratio)))
                / (1 + np.exp(-therm_ratio))
            )
            s2 *= (
                2 * np.pi
            )  # We assume that system energies are given in units of frequency
            return s2

        noise_op1 = self.n_1_operator()
        noise_op2 = self.n_2_operator()

        rate_1 = self.t1(
            i=i,
            j=j,
            noise_op=noise_op1,
            spectral_density=spectral_density1,
            total=total,
            esys=esys,
            get_rate=True,
        )
        rate_2 = self.t1(
            i=i,
            j=j,
            noise_op=noise_op2,
            spectral_density=spectral_density2,
            total=total,
            esys=esys,
            get_rate=True,
        )

        if get_rate:
            return rate_1 + rate_2
        else:
            return 1 / (rate_1 + rate_2)

    def t1_purcell(
        self,
        i: int = 1,
        j: int = 0,
        Q_cap: Union[float, Callable] = None,
        T: float = NOISE_PARAMS["T"],
        total: bool = True,
        esys: Tuple[ndarray, ndarray] = None,
        get_rate: bool = False,
    ) -> float:
        r""":math:`T_1` due to dielectric dissipation in the shunt capacitor.

        References:  Nguyen et al (2019), Smith et al (2020)

        Parameters
        ----------
        i: int >=0
            state index that along with j defines a transition (i->j)
        j: int >=0
            state index that along with i defines a transition (i->j)
        Q_cap
            capacitive quality factor; a fixed value or function of `omega`
        T:
            temperature in Kelvin
        total:
            if False return a time/rate associated with a transition from state i to state j.
            if True return a time/rate associated with both i to j and j to i transitions
        esys:
            evals, evecs tuple
        get_rate:
            get rate or time

        Returns
        -------
        time or rate
            decoherence time in units of :math:`2\pi` (system units), or rate
            in inverse units.
        """
        if "t1_purcell" not in self.supported_noise_channels():
            raise RuntimeError(
                "Noise channel 't1_purcell' is not supported in this system."
            )

        if Q_cap is None:
            # See Smith et al (2020)
            def q_cap_fun(omega):
                return (
                    1e6
                    * (2 * np.pi * 6e9 / np.abs(units.to_standard_units(omega))) ** 0.7
                )

        elif callable(Q_cap):  # Q_cap is a function of omega
            q_cap_fun = Q_cap
        else:  # Q_cap is given as a number

            def q_cap_fun(omega):
                return Q_cap

        def spectral_density(omega, T):
            r"""Calculates the spectral density from the angular frequency and
            temperature.

            Parameters
            ----------
            omega:
                angular frequency in system units (units of frequency)
            T:
                temperature in Kelvin

            Returns
            -------
            Spectral density
            """
            therm_ratio = calc_therm_ratio(omega, T)
            s = (
                2
                * 8
                * self.EC
                / q_cap_fun(omega)
                * (1 / np.tanh(0.5 * np.abs(therm_ratio)))
                / (1 + np.exp(-therm_ratio))
            )
            s *= (
                2 * np.pi
            )  # We assume that system energies are given in units of frequency
            return s

        noise_op = self.n_zeta_operator()

        return self.t1(
            i=i,
            j=j,
            noise_op=noise_op,
            spectral_density=spectral_density,
            total=total,
            esys=esys,
            get_rate=get_rate,
        )


# -Cosine two phi qubit ----------------------------------------------------------------
class Cos2PhiQubit(base.QubitBaseClass, serializers.Serializable, NoisyCos2PhiQubit):
    r"""Cosine Two Phi Qubit implementation with full numerical diagonalization.

    This class implements the Cosine-2-phi qubit, a superconducting qubit design
    featuring two inductors and two Josephson junctions, with a three-dimensional
    Hilbert space. The qubit is designed to have protection against certain noise
    channels through its circuit topology.

    The Hamiltonian, as described in Smith et al. (2020), involves three degrees of
    freedom: φ, ζ, and θ. The variables φ and ζ are treated as harmonic oscillator
    modes, while θ is represented in the charge basis.

    The full Hamiltonian is:

    .. math::

        H = & \,2 E_\text{CJ}'n_\phi^2 + 2 E_\text{CJ}' (n_\theta - n_\text{g} - n_\zeta)^2 + 4 E_\text{C} n_\zeta^2\\
        & + E_\text{L}'(\phi - \pi\Phi_\text{ext}/\Phi_0)^2 + E_\text{L}' \zeta^2 - 2 E_\text{J}\cos{\theta}\cos{\phi} \\
        & + 2 dE_\text{J} E_\text{J}\sin{\theta}\sin{\phi} \\
        & - 4 dC_\text{J} E_\text{CJ}' n_\phi (n_\theta - n_\text{g}-n_\zeta) \\
        & + dL E_\text{L}'(2\phi - \varphi_\text{ext})\zeta ,

    where :math:`E_\text{CJ}' = E_\text{CJ} / (1 - dC_\text{J})^2` and
    :math:`E_\text{L}' = E_\text{L} / (1 - dL)^2`.

    Reference: Smith et al., NPJ Quantum Inf. 6, 8 (2020)
    http://www.nature.com/articles/s41534-019-0231-2

    Parameters
    ----------
    EJ: float
        Josephson energy of the two junctions (in frequency units)
    ECJ: float
        Charging energy of the two junctions (in frequency units)
    EL: float
        Inductive energy of the two inductors (in frequency units)
    EC: float
        Charging energy of the shunt capacitor (in frequency units)
    dCJ: float
        Relative disorder in junction charging energies (C_J1-C_J2)/(C_J1+C_J2)
    dL: float
        Relative disorder in inductive energies (L_1-L_2)/(L_1+L_2)
    dEJ: float
        Relative disorder in junction energies (E_J1-E_J2)/(E_J1+E_J2)
    flux: float
        External magnetic flux in units of the flux quantum
    ng: float
        Offset charge
    ncut: int
        Charge basis cutoff, -ncut <= n_θ <= ncut
    zeta_cut: int
        Number of harmonic oscillator basis states for the ζ variable
    phi_cut: int
        Number of harmonic oscillator basis states for the φ variable
    truncated_dim: int
        Desired dimension of the truncated quantum system; expected: truncated_dim > 1
    id_str: str, optional
        Identifier string for this instance
    esys_method:
        Method for eigenvalue system diagonalization
    esys_method_options:
        Options for eigenvalue system diagonalization
    evals_method:
        Method for eigenvalue diagonalization
    evals_method_options:
        Options for eigenvalue diagonalization
    """

    EJ = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    ECJ = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    EL = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    EC = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    dCJ = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    dL = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    dEJ = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    flux = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    ng = descriptors.WatchedProperty(float, "QUANTUMSYSTEM_UPDATE")
    ncut = descriptors.WatchedProperty(int, "QUANTUMSYSTEM_UPDATE")
    zeta_cut = descriptors.WatchedProperty(int, "QUANTUMSYSTEM_UPDATE")
    phi_cut = descriptors.WatchedProperty(int, "QUANTUMSYSTEM_UPDATE")

    def __init__(
        self,
        EJ: float,
        ECJ: float,
        EL: float,
        EC: float,
        dL: float,
        dCJ: float,
        dEJ: float,
        flux: float,
        ng: float,
        ncut: int,
        zeta_cut: int,
        phi_cut: int,
        truncated_dim: int = 6,
        id_str: Optional[str] = None,
        evals_method: Union[Callable, str, None] = None,
        evals_method_options: Union[dict, None] = None,
        esys_method: Union[Callable, str, None] = None,
        esys_method_options: Union[dict, None] = None,
    ) -> None:
        base.QubitBaseClass.__init__(
            self,
            id_str=id_str,
            evals_method=evals_method,
            evals_method_options=evals_method_options,
            esys_method=esys_method,
            esys_method_options=esys_method_options,
        )
        self.EJ = EJ
        self.ECJ = ECJ
        self.EL = EL
        self.EC = EC
        self.dL = dL
        self.dCJ = dCJ
        self.dEJ = dEJ
        self.flux = flux
        self.ng = ng
        self.ncut = ncut
        self.zeta_cut = zeta_cut
        self.phi_cut = phi_cut
        self.truncated_dim = truncated_dim
        self._default_phi_grid = discretization.Grid1d(-4 * np.pi, 4 * np.pi, 100)
        self._default_zeta_grid = discretization.Grid1d(-4 * np.pi, 4 * np.pi, 100)
        self._default_theta_grid = discretization.Grid1d(-0.5 * np.pi, 1.5 * np.pi, 100)

    @staticmethod
    def default_params() -> Dict[str, Any]:
        r"""Returns a dictionary of default parameters for the Cos2PhiQubit.

        These parameters provide a starting point for typical simulations of the
        Cos2Phi qubit with reasonable values based on experimental implementations.

        Returns
        -------
        Dict[str, Any]
            Dictionary with default values for EJ, ECJ, EL, EC, dCJ, dL, dEJ,
            flux, ng, ncut, zeta_cut, and phi_cut
        """
        return {
            "EJ": 15.0,
            "ECJ": 2.0,
            "EL": 1.0,
            "EC": 0.04,
            "dCJ": 0.0,
            "dL": 0.6,
            "dEJ": 0.0,
            "flux": 0.5,
            "ng": 0.0,
            "ncut": 7,
            "zeta_cut": 30,
            "phi_cut": 7,
        }

    @classmethod
    def create(cls) -> "Cos2PhiQubit":
        r"""Method to create a Cosine Two Phi Qubit.

        Parameters
        ----------
        cls:
            Class type of Cos2PhiQubit

        Returns
        -------
        Cos Two Phi Qubit
        """
        init_params = cls.default_params()
        cosinetwophiqubit = cls(**init_params)
        cosinetwophiqubit.widget()
        return cosinetwophiqubit

    @classmethod
    def supported_noise_channels(cls) -> List[str]:
        r"""Return a list of supported noise channels.

        Parameters
        ----------
        cls:
            Class Method Instance

        Returns
        -------
        List of strings
        """
        return [
            "tphi_1_over_f_cc",
            "tphi_1_over_f_flux",
            "tphi_1_over_f_ng",
            "t1_capacitive",
            "t1_inductive",
            "t1_purcell",
        ]

    def _dim_phi(self) -> int:
        r"""
        Returns Hilbert space dimension of :math:`\phi`
        degree of freedom

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Integer
        """
        return self.phi_cut

    def _dim_zeta(self) -> int:
        r"""
        Returns Hilbert space dimension of :math:`\zeta`
        degree of freedom

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Integer
        """
        return self.zeta_cut

    def _dim_theta(self) -> int:
        r"""
        Returns Hilbert space dimension of :math:`\theta` degree of freedom

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Integer
        """
        return 2 * self.ncut + 1

    def hilbertdim(self) -> int:
        r"""Returns total Hilbert space dimension.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Integer
        """
        return self._dim_phi() * self._dim_zeta() * self._dim_theta()

    def _disordered_el(self) -> float:
        r"""Returns inductive energy renormalized by with disorder.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Float
        """
        return self.EL / (1 - self.dL**2)

    def _disordered_ecj(self) -> float:
        r"""Returns junction capacitance energy renormalized by with disorder.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Float
        """
        return self.ECJ / (1 - self.dCJ**2)

    def phi_osc(self) -> float:
        r"""
        Returns oscillator strength of :math:`\phi` degree of freedom.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Float
        """
        return (2 * self._disordered_ecj() / self._disordered_el()) ** 0.25

    def zeta_osc(self) -> float:
        r"""
        Returns oscillator strength of :math:`\zeta` degree of freedom.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Float
        """
        return (4 * self.EC / self._disordered_el()) ** 0.25

    def phi_plasma(self) -> float:
        r"""
        Returns plasma oscillation frequency of :math:`\phi` degree of freedom.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Float
        """
        return math.sqrt(8.0 * self._disordered_el() * self._disordered_ecj())

    def zeta_plasma(self) -> float:
        r"""
        Returns plasma oscillation frequency of :math:`\zeta` degree of freedom.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Float
        """
        return math.sqrt(16.0 * self.EC * self._disordered_el())

    def _phi_operator(self) -> csc_matrix:
        r"""Returns `phi` operator in the harmonic oscillator basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        dimension = self._dim_phi()
        return (
            (op.creation_sparse(dimension) + op.annihilation_sparse(dimension))
            * self.phi_osc()
            / math.sqrt(2)
        )

    def phi_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns the :math:`\phi` operator in the native or eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns :math:`\phi` operator in the native basis.
            If `True`, the energy eigenspectrum is computed, returns :math:`\phi` operator in
            the energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues and
            energy eigenvectors), returns :math:`\phi` operator in the energy eigenbasis, and does
            not have to recalculate eigenspectrum.

        Returns
        -------
            :math:`\phi` operator in chosen basis. If native basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless energy_esys is specified, :math:`\phi` operator has dimensions of truncated_dim
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy basis is
            chosen, :math:`\phi` operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray.
        """
        native = self._kron3(
            self._phi_operator(), self._identity_zeta(), self._identity_theta()
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def _n_phi_operator(self) -> csc_matrix:
        r"""Returns `n_\phi` operator in the harmonic oscillator basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        dimension = self._dim_phi()
        return (
            1j
            * (op.creation_sparse(dimension) - op.annihilation_sparse(dimension))
            / (self.phi_osc() * math.sqrt(2))
        )

    # changed expected from csc_matrix to Union[ndarray, coo_matrix]
    def n_phi_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, coo_matrix]:
        r"""Returns the :math:`n_\phi` operator in the harmonic oscillator or eigenenergy
        basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns :math:`n_\phi` operator in the native basis.
            If `True`, the energy eigenspectrum is computed, returns :math:`n_\phi` operator
            in the energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns :math:`n_\phi` operator in the energy eigenbasis,
            and does not have to recalculate eigenspectrum.

        Returns
        -------
            :math:`n_\phi` operator in chosen basis. If native basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless energy_esys is specified, :math:`n_\phi` operator has dimensions of truncated_dim
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy basis is
            chosen, :math:`n_\phi` operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray :math:`\zeta`.
        """
        native = self._kron3(
            self._n_phi_operator(), self._identity_zeta(), self._identity_theta()
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def _zeta_operator(self) -> csc_matrix:
        r"""Returns `zeta` operator in the harmonic oscillator basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        dimension = self._dim_zeta()
        return (
            (op.creation_sparse(dimension) + op.annihilation_sparse(dimension))
            * self.zeta_osc()
            / math.sqrt(2)
        )

    def zeta_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns the :math:`\zeta`  operator in the harmonic oscillator or eigenenergy
        basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns :math:`\zeta` operator in the native basis.
            If `True`, the energy eigenspectrum is computed, returns :math:`\zeta`  operator
            in the energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns :math:`\zeta`  operator in the energy eigenbasis,
            and does not have to recalculate eigenspectrum.

        Returns
        -------
            :math:`\zeta`  operator in chosen basis. If native basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless energy_esys is specified, :math:`\zeta`  operator has dimensions of truncated_dim
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy basis is
            chosen, :math:`\zeta` operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray.
        """
        native = self._kron3(
            self._identity_phi(), self._zeta_operator(), self._identity_theta()
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def _n_zeta_operator(self) -> csc_matrix:
        r"""Returns `n_\zeta` operator in the harmonic oscillator basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        dimension = self._dim_zeta()
        return (
            1j
            * (op.creation_sparse(dimension) - op.annihilation_sparse(dimension))
            / (self.zeta_osc() * math.sqrt(2))
        )

    def n_zeta_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns the :math:`n_\zeta`  operator in the harmonic oscillator or
        eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns :math:`n_\zeta` operator in the native basis.
            If `True`, the energy eigenspectrum is computed, returns :math:`n_\zeta`  operator
            in the energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns :math:`n_\zeta`  operator in the energy eigenbasis,
            and does not have to recalculate eigenspectrum.

        Returns
        -------
            :math:`n_\zeta`  operator in chosen basis. If native basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless energy_esys is specified, :math:`n_\zeta`  operator has dimensions of
            truncated_dim x truncated_dim, and is returned as an ndarray. Otherwise, if
            eigenenergy basis is chosen, :math:`n_\zeta` operator has dimensions of m x m,
            for m given eigenvectors, and is returned as an ndarray.
        """
        native = self._kron3(
            self._identity_phi(), self._n_zeta_operator(), self._identity_theta()
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def _exp_i_phi_operator(self) -> csc_matrix:
        r"""Returns `e^{i*phi}` operator in the  harmonic oscillator basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        exponent = 1j * self._phi_operator()
        return sp.sparse.linalg.expm(exponent)

    def _cos_phi_operator(self) -> csc_matrix:
        r"""Returns `cos phi` operator in the harmonic oscillator basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        cos_phi_op = 0.5 * self._exp_i_phi_operator()
        cos_phi_op += cos_phi_op.conj().T
        return cos_phi_op

    def _sin_phi_operator(self) -> csc_matrix:
        r"""Returns `sin phi/2` operator in the LC harmonic oscillator basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        sin_phi_op = -1j * 0.5 * self._exp_i_phi_operator()
        sin_phi_op += sin_phi_op.conj().T
        return sin_phi_op

    def _n_theta_operator(self) -> csc_matrix:
        r"""
        Returns :math:`n_\theta` operator in the charge basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        diag_elements = np.arange(-self.ncut, self.ncut + 1)
        return dia_matrix(
            (diag_elements, [0]), shape=(self._dim_theta(), self._dim_theta())
        ).tocsc()

    def n_theta_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""
        Returns the :math:`n_\theta` operator in the charge or eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns :math:`n_\theta` operator in the charge basis.
            If `True`, the energy eigenspectrum is computed, returns :math:`n_\theta`
            operator in the energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns :math:`n_\theta`  operator in the energy eigenbasis,
            and does not have to recalculate eigenspectrum.

        Returns
        -------
            :math:`n_\theta`  operator in chosen basis. If charge basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless energy_esys is specified, :math:`n_\theta`  operator has dimensions of
            truncated_dim x truncated_dim, and is returned as an ndarray. Otherwise, if
            eigenenergy basis is chosen, :math:`n_\theta` operator has dimensions of m x m,
            for m given eigenvectors, and is returned as an ndarray.
        """
        native = self._kron3(
            self._identity_phi(), self._identity_zeta(), self._n_theta_operator()
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def _cos_theta_operator(self) -> csc_matrix:
        r"""Returns operator :math:`\cos \theta` in the charge basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        cos_op = (
            0.5
            * sparse.dia_matrix(
                (np.ones(self._dim_theta()), [1]),
                shape=(self._dim_theta(), self._dim_theta()),
            ).tocsc()
        )
        cos_op += (
            0.5
            * sparse.dia_matrix(
                (np.ones(self._dim_theta()), [-1]),
                shape=(self._dim_theta(), self._dim_theta()),
            ).tocsc()
        )
        return cos_op

    def _sin_theta_operator(self) -> csc_matrix:
        r"""Returns operator :math:`\sin \theta` in the charge basis.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        sin_op = (
            0.5
            * sparse.dia_matrix(
                (np.ones(self._dim_theta()), [-1]),
                shape=(self._dim_theta(), self._dim_theta()),
            ).tocsc()
        )
        sin_op -= (
            0.5
            * sparse.dia_matrix(
                (np.ones(self._dim_theta()), [1]),
                shape=(self._dim_theta(), self._dim_theta()),
            ).tocsc()
        )
        return sin_op * (-1j)

    def _kron3(self, mat1, mat2, mat3) -> csc_matrix:
        r"""Returns Kronecker product of three matrices.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        return sparse.kron(sparse.kron(mat1, mat2), mat3)

    def _identity_phi(self) -> csc_matrix:
        r"""
        Returns Identity operator acting only on the :math:`\phi`
        Hilbert subspace.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        dimension = self._dim_phi()
        return sparse.eye(dimension)

    def _identity_zeta(self) -> csc_matrix:
        r"""Returns Identity operator acting only on the :math:`\zeta` Hilbert subspace.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        dimension = self._dim_zeta()
        return sparse.eye(dimension)

    def _identity_theta(self) -> csc_matrix:
        r"""Returns Identity operator acting only on the :math:`\theta` Hilbert subspace.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        dimension = self._dim_theta()
        return sparse.eye(dimension)

    def total_identity(self) -> csc_matrix:
        r"""Returns Identity operator acting on the total Hilbert space.

        Parameters
        ----------
        self:
            Method Instance

        Returns
        -------
        Compressed Sparse Column Matrix
        """
        return self._kron3(
            self._identity_phi(), self._identity_zeta(), self._identity_theta()
        )

    def hamiltonian(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> csc_matrix:
        r"""Constructs and returns the Hamiltonian of the Cos2PhiQubit.

        The Hamiltonian is built term by term in the native basis (φ and ζ in
        harmonic oscillator bases, θ in charge basis). It includes kinetic terms
        for all three degrees of freedom, potential terms for the inductors,
        Josephson junction terms, and disorder terms.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns Hamiltonian in the native basis.
            If `True`, computes and returns Hamiltonian in the energy eigenbasis
            (diagonal matrix of eigenvalues).
            If a tuple `(evals, evecs)`, returns Hamiltonian in the basis provided
            by `evecs`.

        Returns
        -------
        Union[ndarray, csc_matrix]
            The Hamiltonian operator. If in native basis, returned as a sparse matrix.
            If in eigenbasis with energy_esys=True, returns a diagonal array of
            eigenvalues with dimensions truncated_dim × truncated_dim.
            If in eigenbasis with specific (evals, evecs), dimensions match the
            provided eigenvectors.
        """
        phi_osc_mat = self._kron3(
            op.number_sparse(self._dim_phi(), self.phi_plasma()),
            self._identity_zeta(),
            self._identity_theta(),
        )

        zeta_osc_mat = self._kron3(
            self._identity_phi(),
            op.number_sparse(self._dim_zeta(), self.zeta_plasma()),
            self._identity_theta(),
        )

        cross_kinetic_mat = (
            2
            * self._disordered_ecj()
            * (
                self.n_theta_operator()
                - self.total_identity() * self.ng
                - self.n_zeta_operator()
            )
            ** 2
        )

        phi_flux_term = self._cos_phi_operator() * np.cos(
            self.flux * np.pi
        ) - self._sin_phi_operator() * np.sin(self.flux * np.pi)
        junction_mat = (
            -2
            * self.EJ
            * self._kron3(
                phi_flux_term, self._identity_zeta(), self._cos_theta_operator()
            )
            + 2 * self.EJ * self.total_identity()
        )

        disorder_l = (
            -2
            * self._disordered_el()
            * self.dL
            * self._kron3(
                self._phi_operator(), self._zeta_operator(), self._identity_theta()
            )
        )

        dis_phi_flux_term = self._sin_phi_operator() * np.cos(
            self.flux * np.pi
        ) + self._cos_phi_operator() * np.sin(self.flux * np.pi)
        disorder_j = (
            2
            * self.EJ
            * self.dEJ
            * self._kron3(
                dis_phi_flux_term, self._identity_zeta(), self._sin_theta_operator()
            )
        )

        dis_c_opt = (
            self._kron3(
                self._n_phi_operator(), self._identity_zeta(), self._n_theta_operator()
            )
            - self.n_phi_operator() * self.ng
            - self._kron3(
                self._n_phi_operator(), self._n_zeta_operator(), self._identity_theta()
            )
        )
        disorder_c = -4 * self._disordered_ecj() * self.dCJ * dis_c_opt

        hamiltonian_mat = (
            phi_osc_mat
            + zeta_osc_mat
            + cross_kinetic_mat
            + junction_mat
            + disorder_l
            + disorder_j
            + disorder_c
        )
        native = hamiltonian_mat.tocsc()
        return self.process_hamiltonian(
            native_hamiltonian=native, energy_esys=energy_esys
        )

    def _evals_calc(self, evals_count) -> ndarray:
        r"""Evaluvates the hamiltonian, and returns the safe eigensvalues.

        Parameters
        ----------
        self:
            Method Instance
        evals_count:
            number of eigenvalues

        Returns
        -------
        Sorted list of eigenvalues
        """
        hamiltonian_mat = self.hamiltonian()
        evals = utils.eigsh_safe(
            hamiltonian_mat,
            k=evals_count,
            return_eigenvectors=False,
            sigma=0.0,
            which="LM",
        )
        return np.sort(evals)

    def _esys_calc(self, evals_count) -> Tuple[ndarray, ndarray]:
        r"""Evaluvates the Hamiltonian and returns the eigenvalues and eigenvectors.

        Parameters
        ----------
        self:
            Method Instance
        evals_count:
            number of eigenvalues

        Returns
        -------
        Eigenvalues, Eigenvectors
        """
        hamiltonian_mat = self.hamiltonian()
        evals, evecs = utils.eigsh_safe(
            hamiltonian_mat,
            k=evals_count,
            return_eigenvectors=True,
            sigma=0.0,
            which="LM",
        )
        evals, evecs = utils.order_eigensystem(evals, evecs)
        return evals, evecs

    def potential(self, phi, zeta, theta) -> float:
        r"""Evaluates the potential energy function at given coordinate values.

        Computes the potential energy part of the Hamiltonian:

        .. math::
            U(\phi,\zeta,\theta) = E_L'(\phi^2 + \zeta^2) - 2E_J\cos\theta\cos(\phi+\pi\cdot\text{flux})
            + 2dE_J\cdot E_J\sin(\phi+\pi\cdot\text{flux})\sin\theta

        Note that disorder in inductors (dL) is incorporated into E_L'. Also, the
        flux is incorporated into the arguments of the trigonometric functions.

        Parameters
        ----------
        phi: float or ndarray
            Value(s) of the phase variable φ
        zeta: float or ndarray
            Value(s) of the phase variable ζ
        theta: float or ndarray
            Value(s) of the phase variable θ

        Returns
        -------
        Union[float, ndarray]
            The potential energy at the specified coordinates
        """
        return (
            self._disordered_el() * (phi * phi)
            + self._disordered_el() * (zeta * zeta)
            - 2 * self.EJ * np.cos(theta) * np.cos(phi + np.pi * self.flux)
            + 2 * self.dEJ * self.EJ * np.sin(phi + np.pi * self.flux) * np.sin(theta)
        )

    def reduced_potential(self, phi, theta) -> float:
        r"""Returns reduced potential by setting :math:`zeta = 0`.

        Parameters
        ----------
        self:
            Method Instance
        phi: float or ndarray
            float value of variable `phi`
        theta: float or ndarray
            float value of variable `theta`

        Returns
        -------
        Float
        """
        return self.potential(phi, 0, theta)

    def plot_potential(
        self, phi_grid=None, theta_grid=None, contour_vals=None, **kwargs
    ) -> Tuple[Figure, Axes]:
        r"""
        Draw contour plot of the potential energy in :math:`\theta, \phi` basis,
        at :math:`\zeta = 0`.

        Parameters
        ----------
        phi_grid: Grid1d, option
            used for setting a custom grid for phi; if None use self._default_phi_grid
        theta_grid: Grid1d, option
            used for setting a custom grid for theta; if None use
            self._default_theta_grid
        contour_vals: list, optional
        **kwargs:
            plotting parameters

        Returns
        -------
        Figure, Axes
        """
        phi_grid = phi_grid or self._default_phi_grid
        theta_grid = theta_grid or self._default_theta_grid

        y_vals = theta_grid.make_linspace()
        x_vals = phi_grid.make_linspace()
        return plot.contours(
            x_vals,
            y_vals,
            self.reduced_potential,
            contour_vals=contour_vals,
            ylabel=r"$\theta$",
            xlabel=r"$\phi$",
            **kwargs,
        )

    def wavefunction(
        self, esys=None, which=0, phi_grid=None, zeta_grid=None, theta_grid=None
    ) -> WaveFunctionOnGrid:
        r"""Computes the 3D wavefunction in the φ-ζ-θ coordinate space.

        This method constructs the real-space representation of a specific eigenstate.
        It transforms the eigenvector (initially expressed in the product basis)
        into a 3D grid representation by using harmonic oscillator wavefunctions
        for φ and ζ, and plane waves for θ.

        Parameters
        ----------
        esys: Tuple[ndarray, ndarray], optional
            Eigenvalues and eigenvectors. If None, they are computed.
        which: int, optional
            Index of the eigenstate to be computed (default=0 for ground state)
        phi_grid: Grid1d, optional
            Grid for the φ coordinate. If None, a default grid is used.
        zeta_grid: Grid1d, optional
            Grid for the ζ coordinate. If None, a default grid is used.
        theta_grid: Grid1d, optional
            Grid for the θ coordinate. If None, a default grid is used.

        Returns
        -------
        WaveFunctionOnGrid
            Object containing the wavefunction amplitudes on the specified 3D grid
        """
        evals_count = max(which + 1, 3)
        if esys is None:
            _, evecs = self.eigensys(evals_count)
        else:
            _, evecs = esys

        phi_grid = phi_grid or self._default_phi_grid
        zeta_grid = zeta_grid or self._default_zeta_grid
        theta_grid = theta_grid or self._default_theta_grid

        phi_basis_labels = phi_grid.make_linspace()
        zeta_basis_labels = zeta_grid.make_linspace()
        theta_basis_labels = theta_grid.make_linspace()

        wavefunc_basis_amplitudes = evecs[:, which].reshape(
            self._dim_phi(), self._dim_zeta(), self._dim_theta()
        )
        wavefunc_amplitudes = np.zeros(
            (phi_grid.pt_count, zeta_grid.pt_count, theta_grid.pt_count),
            dtype=np.complex128,
        )
        for i in range(self._dim_phi()):
            for j in range(self._dim_zeta()):
                for k in range(self._dim_theta()):
                    n_phi, n_zeta, n_theta = i, j, k - self.ncut
                    phi_wavefunc_amplitudes = osc.harm_osc_wavefunction(
                        n_phi, phi_basis_labels, self.phi_osc()
                    )
                    zeta_wavefunc_amplitudes = osc.harm_osc_wavefunction(
                        n_zeta, zeta_basis_labels, self.zeta_osc()
                    )
                    theta_wavefunc_amplitudes = (
                        np.exp(-1j * n_theta * theta_basis_labels) / (2 * np.pi) ** 0.5
                    )
                    wavefunc_amplitudes += wavefunc_basis_amplitudes[
                        i, j, k
                    ] * np.tensordot(
                        np.tensordot(
                            phi_wavefunc_amplitudes, zeta_wavefunc_amplitudes, 0
                        ),
                        theta_wavefunc_amplitudes,
                        0,
                    )

        grid3d = discretization.GridSpec(
            np.asarray(
                [
                    [phi_grid.min_val, phi_grid.max_val, phi_grid.pt_count],
                    [zeta_grid.min_val, zeta_grid.max_val, zeta_grid.pt_count],
                    [theta_grid.min_val, theta_grid.max_val, theta_grid.pt_count],
                ]
            )
        )
        return storage.WaveFunctionOnGrid(grid3d, wavefunc_amplitudes)

    def plot_wavefunction(
        self,
        esys=None,
        which=0,
        phi_grid=None,
        theta_grid=None,
        mode="abs",
        zero_calibrate=True,
        **kwargs,
    ) -> Tuple[Figure, Axes]:
        r"""
        Plots a 2D wave function in :math:`\theta, \phi` basis, at :math:`\zeta = 0`

        Parameters
        ----------
        esys: ndarray, ndarray
            eigenvalues, eigenvectors as obtained from `.eigensystem()`
        which: int, optional
            index of wave function to be plotted (default value = 0)
        phi_grid: Grid1d, option
            used for setting a custom grid for :math:`\phi`; if None use self._default_phi_grid
        theta_grid: Grid1d, option
            used for setting a custom grid for :math:`\theta`; if None use
            self._default_theta_grid
        mode: str, optional
            choices as specified in `constants.MODE_FUNC_DICT` (default value = 'abs')
        zero_calibrate: bool, optional
            if True, colors are adjusted to use zero wavefunction amplitude as the neutral
            color in the palette
        **kwargs:
            plot options

        Returns
        -------
        Figure, Axes
        """
        phi_grid = phi_grid or self._default_phi_grid
        zeta_grid = discretization.Grid1d(0, 0, 1)
        theta_grid = theta_grid or self._default_theta_grid

        amplitude_modifier = constants.MODE_FUNC_DICT[mode]
        wavefunc = self.wavefunction(
            esys,
            phi_grid=phi_grid,
            zeta_grid=zeta_grid,
            theta_grid=theta_grid,
            which=which,
        )

        wavefunc.gridspec = discretization.GridSpec(
            np.asarray(
                [
                    [phi_grid.min_val, phi_grid.max_val, phi_grid.pt_count],
                    [theta_grid.min_val, theta_grid.max_val, theta_grid.pt_count],
                ]
            )
        )
        wavefunc.amplitudes = np.transpose(
            amplitude_modifier(
                utils.standardize_phases(
                    wavefunc.amplitudes.reshape(phi_grid.pt_count, theta_grid.pt_count)
                )
            )
        )
        return plot.wavefunction2d(
            wavefunc,
            zero_calibrate=zero_calibrate,
            ylabel=r"$\theta$",
            xlabel=r"$\phi$",
            **kwargs,
        )

    def phi_1_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns operator representing the phase across inductor 1 in harmonic
        oscillator or eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the harmonic oscillator basis.
            If `True`, the energy eigenspectrum is computed, returns operator in the
            energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns operator in the energy eigenbasis, and does not
            have to recalculate eigenspectrum.

        Returns
        -------
            Operator in chosen basis. If harmonic oscillator basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless `energy_esys` is specified, operator has dimensions of :attr:`truncated_dim`
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy
            basis is chosen, operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray.
        """
        native = self.zeta_operator() - self.phi_operator()
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def phi_2_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns operator representing the phase across inductor 2 in harmonic
        oscillator or eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the harmonic oscillator basis.
            If `True`, the energy eigenspectrum is computed, returns operator in the
            energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns operator in the energy eigenbasis, and does not
            have to recalculate eigenspectrum.

        Returns
        -------
            Operator in chosen basis. If harmonic oscillator basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless `energy_esys` is specified, operator has dimensions of :attr:`truncated_dim`
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy
            basis is chosen, operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray.
        """
        native = -self.zeta_operator() - self.phi_operator()
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def n_1_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns operator representing the charge difference across junction 1 in
        native or eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the harmonic oscillator basis.
            If `True`, the energy eigenspectrum is computed, returns operator in the
            energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns operator in the energy eigenbasis, and does not
            have to recalculate eigenspectrum.

        Returns
        -------
            Operator in chosen basis. If harmonic oscillator basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless `energy_esys` is specified, operator has dimensions of :attr:`truncated_dim`
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy
            basis is chosen, operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray.
        """
        native = 0.5 * self.n_phi_operator() + 0.5 * (
            self.n_theta_operator() - self.n_zeta_operator()
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def n_2_operator(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns operator representing the charge difference across junction 2 in
        native or eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the harmonic oscillator basis.
            If `True`, the energy eigenspectrum is computed, returns operator in the
            energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns operator in the energy eigenbasis, and does not
            have to recalculate eigenspectrum.

        Returns
        -------
            Operator in chosen basis. If harmonic oscillator basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless `energy_esys` is specified, operator has dimensions of :attr:`truncated_dim`
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy
            basis is chosen, operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray.
        """
        native = 0.5 * self.n_phi_operator() - 0.5 * (
            self.n_theta_operator() - self.n_zeta_operator()
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def d_hamiltonian_d_flux(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Calculates the derivative of the Hamiltonian with respect to external flux.

        This operator corresponds to ∂H/∂(flux·π) and is crucial for calculating
        the susceptibility to flux noise and related dephasing effects.

        The flux derivative primarily affects the Josephson junction terms in the
        Hamiltonian, which depend on flux through cos(φ+π·flux) and sin(φ+π·flux).

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the native basis.
            If `True`, returns operator in the energy eigenbasis.
            If a tuple `(evals, evecs)`, returns operator in the basis provided by `evecs`.

        Returns
        -------
        Union[ndarray, csc_matrix]
            The flux derivative operator ∂H/∂(flux·π) in the specified basis
        """
        phi_flux_term = self._sin_phi_operator() * np.cos(
            self.flux * np.pi
        ) + self._cos_phi_operator() * np.sin(self.flux * np.pi)
        junction_mat = (
            2
            * self.EJ
            * self._kron3(
                phi_flux_term, self._identity_zeta(), self._cos_theta_operator()
            )
            * np.pi
        )

        dis_phi_flux_term = self._cos_phi_operator() * np.cos(
            self.flux * np.pi
        ) - self._sin_phi_operator() * np.sin(self.flux * np.pi)
        dis_junction_mat = (
            2
            * self.dEJ
            * self.EJ
            * self._kron3(
                dis_phi_flux_term, self._identity_zeta(), self._sin_theta_operator()
            )
            * np.pi
        )
        native = junction_mat + dis_junction_mat
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def d_hamiltonian_d_EJ(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Returns operator representing a derivative of the Hamiltonian with respect to
        EJ in the native or eigenenergy basis.

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the harmonic oscillator basis.
            If `True`, the energy eigenspectrum is computed, returns operator in the
            energy eigenbasis.
            If `energy_esys = esys`, where esys is a tuple containing two ndarrays (eigenvalues
            and energy eigenvectors), returns operator in the energy eigenbasis, and does not
            have to recalculate eigenspectrum.

        Returns
        -------
            Operator in chosen basis. If harmonic oscillator basis chosen, operator
            returned as a csc_matrix. If the eigenenergy basis is chosen,
            unless `energy_esys` is specified, operator has dimensions of :attr:`truncated_dim`
            x truncated_dim, and is returned as an ndarray. Otherwise, if eigenenergy
            basis is chosen, operator has dimensions of m x m, for m given eigenvectors,
            and is returned as an ndarray.
        """
        phi_flux_term = self._cos_phi_operator() * np.cos(
            self.flux * np.pi
        ) - self._sin_phi_operator() * np.sin(self.flux * np.pi)
        junction_mat = -2 * self._kron3(
            phi_flux_term, self._identity_zeta(), self._cos_theta_operator()
        )

        dis_phi_flux_term = self._sin_phi_operator() * np.cos(
            self.flux * np.pi
        ) + self._cos_phi_operator() * np.sin(self.flux * np.pi)
        dis_junction_mat = (
            2
            * self.dEJ
            * self._kron3(
                dis_phi_flux_term, self._identity_zeta(), self._sin_theta_operator()
            )
        )
        native = junction_mat + dis_junction_mat
        return self.process_op(native_op=native, energy_esys=energy_esys)

    def d_hamiltonian_d_ng(
        self, energy_esys: Union[bool, Tuple[ndarray, ndarray]] = False
    ) -> Union[ndarray, csc_matrix]:
        r"""Calculates the derivative of the Hamiltonian with respect to offset charge ng.
        This operator corresponds to ∂H/∂ng and is crucial for calculating the
        susceptibility to charge noise and related dephasing effects. The offset charge
        ng appears in the Hamiltonian in the cross-kinetic term and in the capacitive
        disorder term, affecting both the energy spectrum and qubit coherence properties.
        The derivative includes two contributions:
        1. From the main kinetic term: -4E_CJ'(n_θ - ng - n_ζ)
        2. From the capacitive disorder term: 4dCJ·E_CJ'·n_φ

        Parameters
        ----------
        energy_esys:
            If `False` (default), returns operator in the native basis.
            If `True`, returns operator in the energy eigenbasis.
            If a tuple `(evals, evecs)`, returns operator in the basis provided by `evecs`.

        Returns
        -------
        Union[ndarray, csc_matrix]
            The charge derivative operator ∂H/∂ng in the specified basis.
            In native basis, returned as a sparse matrix.
            In eigenbasis, returned as a dense array with dimensions determined by
            truncated_dim or the provided eigenvectors.
        """
        native = (
            4 * self.dCJ * self._disordered_ecj() * self.n_phi_operator()
            - 4
            * self._disordered_ecj()
            * (self.n_theta_operator() - self.ng - self.n_zeta_operator())
        )
        return self.process_op(native_op=native, energy_esys=energy_esys)
