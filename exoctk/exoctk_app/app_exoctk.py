## -- IMPORTS
import datetime
import glob
import os
import json
import shutil

import astropy.constants as constants
from astropy.extern.six.moves import StringIO
import astropy.table as at
import astropy.units as u 
import bokeh
from bokeh import mpl
from bokeh.resources import INLINE
from bokeh.util.string import encode_utf8
from bokeh.core.properties import Override
from bokeh.embed import components
from bokeh.models import ColumnDataSource
from bokeh.models import FuncTickFormatter
from bokeh.models import HoverTool
from bokeh.models import Label
from bokeh.models import Range1d
from bokeh.models.widgets import Panel
from bokeh.models.widgets import Tabs
from bokeh.mpl import to_bokeh
from bokeh.plotting import figure
from bokeh.plotting import output_file
from bokeh.plotting import show
from bokeh.plotting import save
import flask
from flask import current_app
from flask import Flask
from flask import make_response
from flask import redirect
from flask import render_template
from flask import request
from flask import send_file
from flask import send_from_directory
from flask import Response
from functools import wraps
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sqlalchemy import *
import sqlite3

import exoctk
from exoctk.modelgrid import ModelGrid
from exoctk.contam_visibility import resolve
from exoctk.contam_visibility import visibilityPA as vpa
from exoctk.contam_visibility import sossFieldSim as fs
from exoctk.contam_visibility import sossContamFig as cf
from exoctk.integrations_groups.integrations_groups import perform_calculation
from exoctk.limb_darkening import limb_darkening_fit as lf
from exoctk.utils import find_closest, filter_table
import log_exoctk
from svo_filters import svo


## -- FLASK SET UP (?)
app_exoctk = Flask(__name__)

# define the cache config keys, remember that it can be done in a settings file
app_exoctk.config['CACHE_TYPE'] = 'null'


MODELGRID_DIR = os.environ.get('MODELGRID_DIR')
INTEGRATIONS_DIR = os.environ.get('INTEGRATIONS_DIR')
FORTGRID_DIR = os.environ.get('FORTGRID_DIR')
EXOCTKLOG_DIR = os.environ.get('EXOCTKLOG_DIR')

# Load the database to log all form submissions
dbpath = os.path.realpath(os.path.join(EXOCTKLOG_DIR,'exoctk_log.db'))
if not os.path.isfile(dbpath):
    log_exoctk.create_db(dbpath)
DB = log_exoctk.load_db(dbpath)

# register the cache instance and binds it on to your app
# cache = Cache(app_exoctk)

# Nice colors for plotting
COLORS = ['blue', 'red', 'green', 'orange', 
          'cyan', 'magenta', 'pink', 'purple']

# Supported profiles
PROFILES = ['uniform', 'linear', 'quadratic', 
            'square-root', 'logarithmic', 'exponential', 
            '3-parameter', '4-parameter']

# Redirect to the index
VERSION = '0.2'
@app_exoctk.route('/')
@app_exoctk.route('/index')

# Load the Index page
def index():
    return render_template('index.html')

# Load the LDC page
@app_exoctk.route('/limb_darkening', methods=['GET', 'POST'])
def limb_darkening():
    # Get all the available filters
    filters = svo.filters()['Band']
    
    # Make HTML for filters
    filt_list = '\n'.join(['<option value="{0}"{1}> {0}</option>'.format(b,\
                ' selected' if b=='Kepler.K' else '') for b in filters])
    
    return render_template('limb_darkening.html', filters=filt_list)
    
# Load the LDC results page
@app_exoctk.route('/limb_darkening_results', methods=['GET', 'POST'])
def limb_darkening_results():
    
    # Log the form inputs
    try:
        log_exoctk.log_form_input(request.form, 'ldc', DB)
    except:
        pass
        
    # Get the input from the form
    modeldir = request.form['modeldir']
    profiles = list(filter(None,[request.form.get(pf) for pf in PROFILES]))
    bandpass = request.form['bandpass']

    # protect against injection attempts
    bandpass = bandpass.replace('<', '&lt')
    profiles = [str(p).replace('<', '&lt') for p in profiles]

    # Get models from local directory if necessary
    if modeldir=='default':
        modeldir = MODELGRID_DIR
    
    # Throw error if input params are invalid
    try:
        teff = int(request.form['teff'])
        logg = float(request.form['logg'])
        feh = float(request.form['feh'])
        mu_min = float(request.form['mu_min'])
    except:
        teff = str(request.form['teff']).replace('<', '&lt')
        logg = str(request.form['logg']).replace('<', '&lt')
        feh = str(request.form['feh']).replace('<', '&lt')
        message = 'Could not calculate limb darkening with the above input parameters.'
        
        return render_template('limb_darkening_error.html', teff=teff, logg=logg, feh=feh, \
                    band=bandpass or 'None', profile=', '.join(profiles), models=modeldir, \
                    message=message)
                    
    n_bins = request.form.get('n_bins')
    pixels_per_bin = request.form.get('pixels_per_bin')
    wl_min = request.form.get('wave_min')
    wl_max = request.form.get('wave_max')
    
    model_grid = ModelGrid(modeldir, resolution=500)
    
    # No data, redirect to the error page
    if not hasattr(model_grid, 'data'):
        message = 'Could not find a model grid to load. Please check your path.'
    
        return render_template('limb_darkening_error.html', teff=teff, logg=logg, feh=feh, \
                    band=bandpass or 'None', profile=', '.join(profiles), models=model_grid.path, \
                    message=message)
        
    else:
        
        if len(model_grid.data)==0:
        
            message = 'Could not calculate limb darkening with the above input parameters.'
        
            return render_template('limb_darkening_error.html', teff=teff, logg=logg, feh=feh, \
                        band=bandpass or 'None', profile=', '.join(profiles), models=model_grid.path, \
                        message=message)
                    
    # Trim the grid to the correct wavelength
    # to speed up calculations, if a bandpass is given
    min_max = model_grid.wave_rng
        
    try:
        
        kwargs = {'n_bins':int(n_bins)} if n_bins else \
                 {'pixels_per_bin':int(pixels_per_bin)} if pixels_per_bin else {}
             
        if wl_min and wl_max:
            kwargs['wl_min'] = float(wl_min)*u.um
            kwargs['wl_max'] = float(wl_max)*u.um
        
        # Make filter object
        bandpass = svo.Filter(bandpass, **kwargs)
        min_max = (bandpass.WavelengthMin,bandpass.WavelengthMax)
        n_bins = bandpass.n_bins
        bp_name = bandpass.filterID
        
        # Get the filter plot
        TOOLS = 'box_zoom,resize,reset'
        bk_plot = figure(tools=TOOLS, title=bp_name, plot_width=400, plot_height=300,
                         x_range=Range1d(bandpass.WavelengthMin,bandpass.WavelengthMax))
                     
        bk_plot.line(*bandpass.raw, line_width=5, color='black', alpha=0.1)
        try:
            for i,(x,y) in enumerate(bandpass.rsr):
                bk_plot.line(x, y, color=(COLORS*5)[i])
        except:
            bk_plot.line(*bandpass.raw)
        
        bk_plot.xaxis.axis_label = 'Wavelength [um]'
        bk_plot.yaxis.axis_label = 'Throughput'
        
        js_resources = INLINE.render_js()
        css_resources = INLINE.render_css()
        filt_script, filt_plot = components(bk_plot)

    except:
        message = 'Insufficient filter information. Please complete the form and try again!'
    
        return render_template('limb_darkening_error.html', teff=teff, logg=logg, feh=feh, \
                    band=bandpass or 'None', profile=', '.join(profiles), models=model_grid.path, \
                    message=message)
                    
    # Trim the grid to nearby grid points to speed up calculation
    full_rng = [model_grid.Teff_vals,model_grid.logg_vals,model_grid.FeH_vals]
    trim_rng = find_closest(full_rng, [teff,logg,feh], n=1, values=True)

    if not trim_rng:

        message = 'Insufficient models grid points to calculate coefficients.'

        return render_template('limb_darkening_error.html', teff=teff, logg=logg, feh=feh, \
                band=bp_name, profile=', '.join(profiles), models=model_grid.path,\
                message=message)

    elif not profiles:

        message = 'No limb darkening profiles have been selected. Please select at least one.'

        return render_template('limb_darkening_error.html', teff=teff, logg=logg, feh=feh, \
                band=bp_name, profile=', '.join(profiles), models=model_grid.path,\
                message=message)

    else:

        try:
            model_grid.customize(Teff_rng=trim_rng[0], logg_rng=trim_rng[1],
                         FeH_rng=trim_rng[2], wave_rng=min_max)

        except:

            message = 'Insufficient wavelength coverage to calculate coefficients.'

            return render_template('limb_darkening_error.html', teff=teff, logg=logg, feh=feh, \
                    band=bp_name, profile=', '.join(profiles), models=model_grid.path,\
                    message=message)
                         
    # Calculate the coefficients for each profile
    ld = lf.LDC(model_grid)
    for prof in profiles:
        ld.calculate(teff, logg, feh, prof, bandpass=bandpass)
                    
    # Draw a figure for each wavelength bin
    tabs = []
    for wav in np.unique(ld.results['wave_eff']):
        dat = ld.results[ld.results['wave_eff'] == wav]
        
        # Plot it
        TOOLS = 'box_zoom,box_select,crosshair,resize,reset,hover'
        fig = figure(tools=TOOLS, x_range=Range1d(0, 1), y_range=Range1d(0, 1),
                     plot_width=800, plot_height=400)
        ld.plot(wave_eff=wav, fig=fig)
                                    
        # Plot formatting
        fig.legend.location = 'bottom_right'
        fig.xaxis.axis_label = 'mu'
        fig.yaxis.axis_label = 'Intensity'
        
        tabs.append(Panel(child=fig, title=str(wav)))
                
    final = Tabs(tabs=tabs)
    
    # Get HTML
    script, div = components(final)
    
    # Store the tables as a string
    file_as_string = str(ld.results[[c for c in ld.results.dtype.names if
                                     ld.results.dtype[c] != object]])
    
    # # Format mu and r_eff vals
    # r_eff = '{:.4f} R_\odot'.format(grid_point['r_eff']*1.438e-11)
    # mu_eff = '{:.4f}'.format(0)
    r_eff = mu_eff = ''
    
    # Make a table for each profile with a row for each wavelength bin
    profile_tables = []
    for profile in profiles:
        
        # Make LaTeX for polynomials
        latex = lf.ld_profile(profile, latex=True)
        poly = '\({}\)'.format(latex).replace('*','\cdot').replace('\e','e')
        
        # Make the table into LaTeX
        table = filter_table(ld.results, profile=profile)
        co_cols = [c for c in ld.results.colnames if (c.startswith('c') or
                   c.startswith('e')) and len(c) == 2 and not
                   np.all([np.isnan(i) for i in table[c]])]
        table = table[['wave_min','wave_max']+co_cols]
        table.rename_column('wave_min', '\(\lambda_\mbox{min}\hspace{5px}(\mu m)\)')
        table.rename_column('wave_max', '\(\lambda_\mbox{max}\hspace{5px}(\mu m)\)')
        
        # Add the results to the lists
        html_table = '\n'.join(table.pformat(max_width=500, html=True))\
                     .replace('<table','<table id="myTable" class="table table-striped table-hover"')
        
        # Add the table title
        header = '<br></br><strong>{}</strong><br><p>\(I(\mu)/I(\mu=1)\) = {}</p>'.format(profile,poly)
        html_table = header+html_table

        profile_tables.append(html_table)
        
    return render_template('limb_darkening_results.html', teff=teff, logg=logg, feh=feh, \
                band=bp_name, mu=mu_eff, profile=', '.join(profiles), \
                r=r_eff, models=model_grid.path, table=profile_tables, \
                script=script, plot=div, file_as_string=repr(file_as_string), \
                filt_plot=filt_plot, filt_script=filt_script, js=js_resources, css=css_resources)

# Load the LDC error page
@app_exoctk.route('/limb_darkening_error', methods=['GET', 'POST'])
def limb_darkening_error():
    return render_template('limb_darkening_error.html')


# Load the integrations and groups page
@app_exoctk.route('/integrations_groups', methods=['GET', 'POST'])
def integrations_groups():

    # Print out pandeia sat values
    with open(INTEGRATIONS_DIR) as f:
        sat_data = json.load(f)['fullwell']
    
    return render_template('integrations_groups.html', sat_data=sat_data)

# Load the integrations and groups results
@app_exoctk.route('/integrations_groups_results', methods=['GET', 'POST'])
def integrations_groups_results():
    
    # Read in parameters from form
    params = {}
    for key in dict(request.form).keys():
        params[key] = dict(request.form)[key][0]
    print(params)
    try:
        err = 0
    
        # Specific error catching
        if params['n_group'].isdigit():
            params['n_group'] = int(params['n_group'])
            if params['n_group'] <= 1:
               err = 'Please try again with at least one group.'
        else:
            if params['n_group'] != 'optimize':
             err = "You need to double check your group input. Please put the number of groups per integration or 'optimize' and we can calculate it for you."
        if (False in [params['mag'].isdigit(), params['obs_time'].isdigit()]) and ('.' not in params['mag']) and ('.' not in params['obs_time']):
            err = 'Your magnitude or observation time is not a number, or you left the field blank.'
    
        else:
            if (4.5 > float(params['mag'])) or (12.5 < float(params['mag'])):
                err = 'Looks like we do not have useful approximations for your magnitude. Could you give us a number between 5.5-12.5?'
            if float(params['obs_time']) <= 0:
                err = 'You have a negative transit time -- I doubt that will be of much use to anyone.'
    
        if float(params['sat_max']) <= 0:
            err = 'You put in an underwhelming saturation level. There is something to be said for being too careful...'
        if (params['sat_mode'] == 'well') and float(params['sat_max']) > 1:
            err = 'You are saturating past the full well. Is that a good idea?'

        if type(err) == str:
            return render_template('integrations_groups_error.html', err=err)
    
        # Only create the dict if the form input looks okay
        # Make sure everything is the right type
        ins = params['ins']
        float_params = ['obs_time', 'mag', 'sat_max']
        str_params = ['mod', 'band', '{}_filt'.format(ins), '{}_ta_filt'.format(ins), 'ins', '{}_subarray'.format(ins), '{}_subarray_ta'.format(ins), 'sat_mode']
        for key in params:
            if key in float_params:
                params[key] = float(params[key])
            if key in str_params:
                params[key] = str(params[key])
    
        # Also get the data path in there
        params['infile'] = INTEGRATIONS_DIR 
    
        # Rename the ins-mode params to more general counterparts
        params['filt'] = params['{}_filt'.format(ins)]
        params['filt_ta'] = params['{}_filt_ta'.format(ins)]
        params['subarray'] = params['{}_subarray'.format(ins)]
        params['subarray_ta'] = params['{}_subarray_ta'.format(ins)]
        results = perform_calculation(params)
    
        if type(results) == dict:
            results_dict = results
            one_group_error = ""
            zero_group_error = ""
            if results_dict['n_group'] == 1:
                one_group_error = 'Be careful! This only predicts one group, and you may be in danger of oversaturating!'
            if results_dict['max_ta_groups'] == 0:
                zero_group_error ='Be careful! This oversaturated the TA in the minimum groups. Consider a different TA setup.'
            if results_dict['max_ta_groups'] == -1:
                zero_group_error = 'This object is too faint to reach the required TA SNR in this filter. Consider a different TA setup.'
                results_dict['min_sat_ta'] = 0
                results_dict['t_duration_ta_max'] = 0
                results_dict['max_sat_ta'] = 0
                results_dict['t_duration_ta_max'] = 0
            if results_dict['max_sat_prediction'] > results_dict['sat_max']:
                one_group_error = 'Hold up! You chose to input your own groups, and you have oversaturated the detector! Proceed with caution!'
            # Do some formatting for a prettier end product
            results_dict['filt'] = results_dict['filt'].upper()
            results_dict['filt_ta'] = results_dict['filt_ta'].upper()
            results_dict['band'] = results_dict['band'].upper()
            results_dict['mod'] = results_dict['mod'].upper()
            if results_dict['ins'] == 'niriss':
                if results_dict['subarray_ta'] == 'nrm':
                    results_dict['subarray_ta'] = 'SUBTASOSS -- BRIGHT'
                else:
                    results_dict['subarray_ta'] = 'SUBTASOSS -- FAINT'
            results_dict['subarray'] = results_dict['subarray'].upper()
            results_dict['subarray_ta'] = results_dict['subarray_ta'].upper()
     
            form_dict = {'miri': 'MIRI', 'nircam': 'NIRCam', 'nirspec': 'NIRSpec', 'niriss': 'NIRISS'}
            results_dict['ins'] = form_dict[results_dict['ins']]
      
            return render_template('integrations_groups_results.html',
                    results_dict=results_dict, one_group_error=one_group_error,
                                   zero_group_error=zero_group_error)
        
        else:
            err = results 
            return render_template('integrations_groups_error.html', err=err)
    
    except IOError:
        err = 'One of you numbers is NOT a number! Please try again!'
    except Exception as e:
        err = 'This is not an error we anticipated, but the error caught was : ' + str(e)
        return render_template('integrations_groups_error.html', err=err)



# Load the contam and visibility page
@app_exoctk.route('/contam_visibility', methods = ['GET', 'POST'])
def contam_visibility():

    contamVars = {}
    if request.method == 'POST':
        tname = request.form['targetname']
        contamVars['tname'] = tname
        contamVars['ra'], contamVars['dec'] = request.form['ra'], request.form['dec']
        contamVars['PAmax'] = request.form['pamax']
        contamVars['PAmin'] = request.form['pamin']

        if request.form['bininfo']!='':
            contamVars['binComp'] = list(map(float, request.form['bininfo'].split(',')))
        else:
            contamVars['binComp'] = request.form['bininfo']
        
        radec = ', '.join([contamVars['ra'], contamVars['dec']])
        
        if contamVars['PAmax']=='':
            contamVars['PAmax'] = 359
        if contamVars['PAmin']=='':
            contamVars['PAmin'] = 0

        if request.form['submit'] == 'Resolve Target':
            contamVars['ra'], contamVars['dec'] = resolve.resolve_target(tname)
            
            return render_template('contam_visibility.html', contamVars = contamVars)
            
        else:
            
            try:

                contamVars['visPA'] = True
    
                # Make plot
                TOOLS = 'crosshair,resize,reset,hover,save'
                fig = figure(tools=TOOLS, plot_width=800, plot_height=400, x_axis_type='datetime', title=contamVars['tname'] or radec)
                pG, pB, dates, vis_plot = vpa.checkVisPA(contamVars['ra'], contamVars['dec'], tname, fig=fig)
    
                # Format x axis
                day0 = datetime.date(2019, 6, 1)
                vis_plot.x_range = Range1d(day0,day0+datetime.timedelta(days=367))
    
                # Get scripts
                vis_js = INLINE.render_js()
                vis_css = INLINE.render_css()
                vis_script, vis_div = components(vis_plot)

                if request.form['submit'] == 'Calculate Visibility and Contamination':
        
                    contamVars['contam'] = True
                    
                    # Make field simulation
                    contam_cube = fs.sossFieldSim(contamVars['ra'], contamVars['dec'], binComp=contamVars['binComp'])
                    contam_plot = cf.contam(contam_cube, contamVars['tname'] or radec, paRange=[int(contamVars['PAmin']),int(contamVars['PAmax'])], badPA=pB, fig='bokeh')
                    
                    
                    # Get scripts
                    contam_js = INLINE.render_js()
                    contam_css = INLINE.render_css()
                    contam_script, contam_div = components(contam_plot)
                
                else:
                
                    contamVars['contam'] = False
                    contam_script = contam_div = contam_js = contam_css = ''
            
                return render_template('contam_visibility_results.html', contamVars=contamVars, \
                        vis_plot=vis_div, vis_script=vis_script, vis_js=vis_js, vis_css=vis_css,\
                        contam_plot=contam_div, contam_script=contam_script, contam_js=contam_js, contam_css=contam_css)

            except IOError:#Exception as e:
                err = 'The following error occurred: ' + str(e)
                return render_template('integrations_groups_error.html', err=err)

    return render_template('contam_visibility.html', contamVars = contamVars)

    
# Save the results to file
@app_exoctk.route('/download', methods=['POST'])
def exoctk_savefile():
    file_as_string = eval(request.form['file_as_string'])
    
    response = make_response(file_as_string)
    response.headers["Content-type"] = 'text; charset=utf-8'
    response.headers["Content-Disposition"] = "attachment; filename=ExoXTK_results.txt"
    return response

def _param_fort_validation(args):
    temp = args.get('ptemp',1000)
    chem = args.get('pchem','noTiO')
    cloud = args.get('cloud','0')
    pmass = args.get('pmass','1.5')
    m_unit = args.get('m_unit','M_jup')
    reference_radius = args.get('refrad',1)
    r_unit = args.get('r_unit','R_jup')
    rstar = args.get('rstar',1)
    rstar_unit = args.get('rstar_unit','R_sun')
    return temp,chem,cloud,pmass,m_unit,reference_radius,r_unit,rstar,rstar_unit

@app_exoctk.route('/fortney', methods=['GET','POST'])
def fortney():
    """
    Pull up Forntey Grid plot the results and download
    """
    
    # Grab the inputs arguments from the URL
    args = flask.request.args
    
    temp,chem,cloud,pmass,m_unit,reference_radius,r_unit,rstar,rstar_unit = _param_fort_validation(args)
    #get sqlite database

    try:
        db = create_engine('sqlite:///'+FORTGRID_DIR)
        header= pd.read_sql_table('header',db)
    except:
        raise Exception('Fortney Grid File Path is incorrect, or not initialized')

    if args:
        rstar=float(rstar)
        rstar = (rstar*u.Unit(rstar_unit)).to(u.km)
        reference_radius = float(reference_radius)
        rplan = (reference_radius*u.Unit(r_unit)).to(u.km)

        #clouds 
        if cloud.find('flat') != -1: 
            flat = int(cloud[4:])
            ray = 0 
        elif cloud.find('ray') != -1: 
            ray = int(cloud[3:])
            flat = 0 
        elif int(cloud) == 0: 
            flat = 0 
            ray = 0     
        else:
            flat = 0 
            ray = 0 
            print('No cloud parameter not specified, default no clouds added')
        
        #chemistry 
        if chem == 'noTiO': 
            noTiO = True
        if chem == 'eqchem': 
            noTiO = False
            #grid does not allow clouds for cases with TiO
            flat = 0 
            ray = 0 

        fort_grav = 25.0*u.m/u.s/u.s

        temp = float(temp)
        df = header.loc[(header.gravity==fort_grav) & (header.temp==temp)
                           & (header.noTiO==noTiO) & (header.ray==ray) &
                           (header.flat==flat)]

        wave_planet=np.array(pd.read_sql_table(df['name'].values[0],db)['wavelength'])[::-1]
        r_lambda=np.array(pd.read_sql_table(df['name'].values[0],db)['radius'])*u.km
        z_lambda = r_lambda- (1.25*u.R_jup).to(u.km) #all fortney models have fixed 1.25 radii
        
        #scale with planetary mass 
        pmass=float(pmass)
        mass = (pmass*u.Unit(m_unit)).to(u.kg)
        gravity = constants.G*(mass)/(rplan.to(u.m))**2.0 #convert radius to m for gravity units
        #scale lambbda (this technically ignores the fact that scaleheight is altitude dependent)
        #therefore, it will not be valide for very very low gravities
        z_lambda = z_lambda*fort_grav/gravity
        
        #create new wavelength dependent R based on scaled ravity
        r_lambda = z_lambda + rplan
        #finally compute (rp/r*)^2
        flux_planet = np.array(r_lambda**2/rstar**2)

        x=wave_planet
        y=flux_planet[::-1]
    else:   
        df= pd.read_sql_table('t1000g25_noTiO',db)
        x, y = df['wavelength'], df['radius']**2.0/7e5**2.0
    
    tab = at.Table(data=[x, y])
    fh = StringIO()
    tab.write(fh, format='ascii.no_header')
    table_string = fh.getvalue()

    fig = figure(plot_width=1100, plot_height=400, responsive=False)
    fig.line(x, 1e6*(y-np.mean(y)), color='Black', line_width=0.5)
    fig.xaxis.axis_label = 'Wavelength (um)'
    fig.yaxis.axis_label = 'Rel. Transit Depth (ppm)'
    
    js_resources = INLINE.render_js()
    css_resources = INLINE.render_css()
    
    script, div = components(fig)
    
    html = flask.render_template(
                                 'fortney.html',
                                 plot_script=script,
                                 plot_div=div,
                                 js_resources=js_resources,
                                 css_resources=css_resources,
                                 temp=list(map(str, header.temp.unique())),
                                 table_string=table_string
                                 )
    return encode_utf8(html)

@app_exoctk.route('/fortney_result', methods=['POST'])
def save_fortney_result():
    table_string = flask.request.form['data_file']
    return flask.Response(table_string, mimetype="text/dat",
                          headers={"Content-disposition":
                          "attachment; filename=fortney.dat"})

@app_exoctk.route('/integrations_groups_download')
def integrations_groups_download():
    return send_file(INTEGRATIONS_DIR, mimetype="text/json", attachment_filename='integrations_groups_input_data.json', as_attachment=True)


@app_exoctk.route('/fortney_download')
def fortney_download():
    fortney_data = FORTGRID_DIR
    return send_file(fortney_data, attachment_filename='fortney_grid.db', as_attachment=True)


def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """
    return username == 'admin' and password == 'secret'

def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated
    
@app_exoctk.route('/admin')
@requires_auth
def secret_page():
    
    tables = [i[0] for i in DB.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(tables)
    
    log_tables = []
    for table in tables:
        
        try:
            data = log_exoctk.view_log(DB, table)
        
            # Add the results to the lists
            html_table = '\n'.join(data.pformat(max_width=500, html=True)).replace('<table','<table id="myTable" class="table table-striped table-hover"')
        
        except:
            html_table = '<p>No data to display</p>'

        # Add the table title
        header = '<h3>{}</h3>'.format(table)
        html_table = header+html_table

        log_tables.append(html_table)
    
    return render_template('admin_page.html', tables=log_tables)


## -- RUN
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app_exoctk.run(host='0.0.0.0', port=port, debug=True)