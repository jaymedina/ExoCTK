#!/usr/bin/python
# -*- coding: latin-1 -*-
"""
A module to calculate limb darkening coefficients from a grid of model spectra
"""
import inspect

import astropy.table as at
import astropy.units as q
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import rc
import numpy as np
from scipy.optimize import curve_fit
from svo_filters import svo

from .. import utils
from .. import modelgrid

rc('font', **{'family': 'sans-serif', 'sans-serif': ['Helvetica']})
rc('text', usetex=True)


def ld_profile(name='quadratic', latex=False):
    """
    Define the function to fit the limb darkening profile

    Reference:
        https://www.cfa.harvard.edu/~lkreidberg/batman/
        tutorial.html#limb-darkening-options

    Parameters
    ----------
    name: str
        The name of the limb darkening profile function to use,
        including 'uniform', 'linear', 'quadratic', 'square-root',
        'logarithmic', 'exponential', '3-parameter', and '4-parameter'
    latex: bool
        Return the function as a LaTeX formatted string

    Returns
    -------
    function, str
        The corresponding function for the given profile

    """
    # Supported profiles a la BATMAN
    names = ['uniform', 'linear', 'quadratic', 'square-root',
             'logarithmic', 'exponential', '3-parameter', '4-parameter']

    # Check that the profile is supported
    if name in names:

        # Uniform
        if name == 'uniform':
            def profile(m, c1):
                return c1

        # Linear
        if name == 'linear':
            def profile(m, c1):
                return 1. - c1*(1.-m)

        # Quadratic
        if name == 'quadratic':
            def profile(m, c1, c2):
                return 1. - c1*(1.-m) - c2*(1.-m)**2

        # Square-root
        if name == 'square-root':
            def profile(m, c1, c2):
                return 1. - c1*(1.-m) - c2*(1.-np.sqrt(m))

        # Logarithmic
        if name == 'logarithmic':
            def profile(m, c1, c2):
                return 1. - c1*(1.-m) - c2*m*np.log(m)

        # Exponential
        if name == 'exponential':
            def profile(m, c1, c2):
                return 1. - c1*(1.-m) - c2/(1.-np.e**m)

        # 3-parameter
        if name == '3-parameter':
            def profile(m, c1, c2, c3):
                return 1. - c1*(1.-m) - c2*(1.-m**1.5) - c3*(1.-m**2)

        # 4-parameter
        if name == '4-parameter':
            def profile(m, c1, c2, c3, c4):
                return 1. - c1*(1.-m**0.5) - c2*(1.-m) \
                          - c3*(1.-m**1.5) - c4*(1.-m**2)

        if latex:
            profile = inspect.getsource(profile).replace('\n', '')
            profile = profile.replace('\\', '').split('return ')[1]

            for i, j in [('**', '^'), ('m', '\mu'), (' ', ''), ('np.', '\\'),
                         ('0.5', '{0.5}'), ('1.5', '{1.5}')]:
                profile = profile.replace(i, j)

        return profile

    else:
        print("'{}' is not a supported profile. Try".format(name), names)
        return


class LDC:
    """A class to hold all the LDCs you want to run

    Example
    -------
    from ExoCTK.limb_darkening import limb_darkening_fit as lf
    from ExoCTK import modelgrid
    from svo_filters import Filter
    fits_files = '/user/jfilippazzo/Models/ACES/default/'
    model_grid = modelgrid.ModelGrid(fits_files, resolution=700)
    ld = lf.LDC(model_grid)
    bp = Filter('WFC3_IR.G141', n_bins=5)
    ld.calculate(4000, 5.0, 0.0, 'quadratic', bandpass=bp)
    ld.calculate(4000, 5.0, 0.0, '4-parameter', bandpass=bp)
    ld.plot()
    """
    def __init__(self, model_grid):
        """Initialize an LDC object

        Parameters
        ----------
        model_grid: ExoCTK.modelgrid.ModelGrid
            The grid of synthetic spectra from which the coefficients will
            be calculated
        """
        # Set the model grid
        if not isinstance(model_grid, modelgrid.ModelGrid):
            raise TypeError("'model_grid' must be a ExoCTK.modelgrid.ModelGrid\
                             object.")

        self.model_grid = model_grid

        # Table for results
        columns = ['Teff', 'logg', 'FeH', 'profile', 'filter', 'coeffs',
                   'errors', 'wave', 'wave_min', 'wave_eff', 'wave_max',
                   'scaled_mu', 'raw_mu', 'mu_min', 'scaled_ld', 'raw_ld',
                   'ld_min', 'ldfunc', 'flux', 'bandpass']
        dtypes = [float, float, float, '|S20', '|S20', object, object, object,
                  np.float16, np.float16, np.float16, object, object,
                  np.float16, object, object, np.float16, object, object,
                  object]
        self.results = at.Table(names=columns, dtype=dtypes)

        self.ld_color = {'quadratic': 'blue', '4-parameter': 'red',
                         'exponential': 'green', 'linear': 'orange',
                         'square-root': 'cyan', '3-parameter': 'magenta',
                         'logarithmic': 'pink', 'uniform': 'purple'}

    @staticmethod
    def bootstrap_errors(mu_vals, func, coeffs, errors, n_samples=1000):
        """
        Bootstrapping LDC errors

        Parameters
        ----------
        mu_vals: sequence
            The mu values
        func: callable
            The LD profile function
        coeffs: sequence
            The coefficients
        errors: sequence
            The errors on each coeff
        n_samples: int
            The number of samples

        Returns
        -------
        tuple
            The lower and upper errors
        """
        # Generate n_samples
        vals = []
        for n in range(n_samples):
            co = np.random.normal(coeffs, errors)
            vals.append(func(mu_vals, *co))

        # r = np.array(list(zip(*vals)))
        dn_err = np.min(np.asarray(vals), axis=0)
        up_err = np.max(np.asarray(vals), axis=0)

        return dn_err, up_err

    def calculate(self, Teff, logg, FeH, profile, mu_min=0.05, ld_min=0.01,
                  bandpass=None, **kwargs):
        """
        Calculates the limb darkening coefficients for a given synthetic
        spectrum. If the model grid does not contain a spectrum of the given
        parameters, the grid is interpolated to those parameters.

        Reference for limb-darkening laws:
        http://www.astro.ex.ac.uk/people/sing/David_Sing/Limb_Darkening.html

        Parameters
        ----------
        Teff: int
            The effective temperature of the model
        logg: float
            The logarithm of the surface gravity
        FeH: float
            The logarithm of the metallicity
        profile: str
            The name of the limb darkening profile function to use,
            including 'uniform', 'linear', 'quadratic', 'square-root',
            'logarithmic', 'exponential', and '4-parameter'
        mu_min: float
            The minimum mu value to consider
        ld_min: float
            The minimum limb darkening value to consider
        bandpass: svo_filters.svo.Filter() (optional)
            The photometric filter through which the limb darkening
            is to be calculated
        """
        # Define the limb darkening profile function
        ldfunc = ld_profile(profile)

        if not ldfunc:
            raise ValueError("No such LD profile:", profile)

        # Get the grid point
        grid_point = self.model_grid.get(Teff, logg, FeH)

        # Retrieve the wavelength, flux, mu, and effective radius
        wave = grid_point.get('wave')
        flux = grid_point.get('flux')
        mu = grid_point.get('mu').squeeze()

        # Use tophat oif no bandpass
        if bandpass is None:
            units = self.model_grid.wl_units
            bandpass = svo.Filter('tophat', wl_min=np.min(wave)*units,
                                  wl_max=np.max(wave)*units)

        # Check if a bandpass is provided
        if not isinstance(bandpass, svo.Filter):
            raise TypeError("Invalid bandpass of type", type(bandpass))

        # Make sure the bandpass has coverage
        bp_min = bandpass.WavelengthMin*q.Unit(bandpass.WavelengthUnit)
        bp_max = bandpass.WavelengthMax*q.Unit(bandpass.WavelengthUnit)
        mg_min = self.model_grid.wave_rng[0]*self.model_grid.wl_units
        mg_max = self.model_grid.wave_rng[-1]*self.model_grid.wl_units
        if bp_min < mg_min or bp_max > mg_max:
            raise ValueError('Bandpass {} not covered by model grid of\
                              wavelength range {}'.format(bandpass.filterID,
                                                          self.model_grid
                                                              .wave_rng))

        # Apply the filter
        flux = bandpass.apply([wave, flux])

        # Make rsr curve 3 dimensions if there is only one
        # wavelength bin, then get wavelength only
        bp = bandpass.rsr
        if len(bp.shape) == 2:
            bp = bp[None, :]
        wave = bp[:, 0, :]

        # Calculate mean intensity vs. mu
        wave = wave[None, :] if len(wave.shape) == 1 else wave
        flux = flux[None, :] if len(flux.shape) == 2 else flux
        mean_i = np.nanmean(flux, axis=-1)
        mean_i[mean_i == 0] = np.nan

        # Calculate limb darkening, I[mu]/I[1] vs. mu
        ld = mean_i/mean_i[:, np.where(mu == max(mu))].squeeze(axis=-1)

        # Rescale mu values to make f(mu=0)=ld_min
        # for the case where spherical models extend beyond limb
        ld_avg = np.nanmean(ld, axis=0)
        muz = np.interp(ld_min, ld_avg, mu) if any(ld_avg < ld_min) else 0
        mu = (mu - muz) / (1 - muz)

        # Trim to useful mu range
        imu, = np.where(mu > mu_min)
        scaled_mu, scaled_ld = mu[imu], ld[:, imu]

        # Fit limb darkening coefficients for each wavelength bin
        for n, ldarr in enumerate(scaled_ld):

            # Fit polynomial to data
            coeffs, cov = curve_fit(ldfunc, scaled_mu, ldarr, method='lm')

            # Calculate errors from covariance matrix diagonal
            errs = np.sqrt(np.diag(cov))

            # Make a dictionary or the results
            result = {}
            result['Teff'] = Teff
            result['logg'] = logg
            result['FeH'] = FeH
            result['filter'] = bandpass.filterID
            result['raw_mu'] = mu
            result['raw_ld'] = ld[n]
            result['scaled_mu'] = scaled_mu
            result['scaled_ld'] = ldarr
            result['flux'] = flux[n]
            result['wave'] = wave[n]
            result['mu_min'] = mu_min
            result['bandpass'] = bandpass
            result['ldfunc'] = ldfunc
            result['coeffs'] = coeffs
            result['errors'] = errs
            result['profile'] = profile
            result['n_bins'] = bandpass.n_bins
            result['pixels_per_bin'] = bandpass.pixels_per_bin
            result['wave_min'] = wave[n, 0].round(5)
            result['wave_eff'] = bandpass.centers[0, n].round(5)
            result['wave_max'] = wave[n, -1].round(5)

            # Add the coeffs
            for n, (err, coeff) in enumerate(zip(coeffs, errs)):
                cname = 'c{}'.format(n + 1)
                ename = 'e{}'.format(n + 1)
                result[cname] = coeff.round(3)
                result[ename] = err.round(3)

                # Add the coefficient column to the table if not present
                if cname not in self.results.colnames:
                    self.results[cname] = [np.nan]*len(self.results)
                    self.results[ename] = [np.nan]*len(self.results)

            # Add the new row to the table
            result = {i: j for i, j in result.items() if i in
                      self.results.colnames}
            self.results.add_row(result)

    def plot(self, fig=None, **kwargs):
        """Plot the LDCs

        Parameters
        ----------
        fig: matplotlib.pyplot.figure, bokeh.plotting.figure (optional)
            An existing figure to plot on
        """
        # Separate plotting kwargs from parameter kwargs
        pwargs = {i: j for i, j in kwargs.items() if i in self.results.columns}
        kwargs = {i: j for i, j in kwargs.items() if i not in pwargs.keys()}

        # Filter the table by given kwargs
        table = utils.filter_table(self.results, **pwargs)

        for row in table:

            # Set color for plot
            color = self.ld_color[row['profile']]

            # Set label for plot
            label = row['profile']

            # Generate smooth curve
            ldfunc = row['ldfunc']
            mu_vals = np.linspace(0, 1, 1000)
            ld_vals = ldfunc(mu_vals, *row['coeffs'])

            # Generate smooth errors
            dn_err, up_err = self.bootstrap_errors(mu_vals, ldfunc,
                                                   row['coeffs'],
                                                   row['errors'])

            # Matplotlib fig by default
            if fig is None:
                fig = plt.gcf()

            # Add fits to matplotlib
            if isinstance(fig, matplotlib.figure.Figure):

                # Make axes
                ax = fig.add_subplot(111)

                # Plot the fitted points
                ax.errorbar(row['raw_mu'], row['raw_ld'], c='k',
                            ls='None', marker='o', markeredgecolor='k',
                            markerfacecolor='None')

                # Plot the mu cutoff
                ax.axvline(row['mu_min'], color='0.5', ls=':')

                # Draw the curve and error
                ax.plot(mu_vals, ld_vals, color=color, label=label, **kwargs)
                ax.fill_between(mu_vals, dn_err, up_err, color=color,
                                alpha=0.1)
                ax.set_ylim(0, 1)
                ax.set_xlim(0, 1)

            # Or to bokeh!
            else:

                # Plot the fitted points
                fig.circle(row['raw_mu'], row['raw_ld'], fill_color='black')

                # Plot the mu cutoff
                fig.line([row['mu_min']]*2, [0, 1], legend='cutoff',
                         line_color='#6b6ecf', line_dash='dotted')

                # Draw the curve and error
                fig.line(mu_vals, ld_vals, line_color=color, legend=label,
                         **kwargs)
                vals = np.append(mu_vals, mu_vals[::-1])
                evals = np.append(dn_err, up_err[::-1])
                fig.patch(vals, evals, color=color, fill_alpha=0.2,
                          line_alpha=0)