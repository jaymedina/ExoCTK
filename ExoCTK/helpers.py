#!/usr/bin/python
# -*- coding: latin-1 -*-
from astropy.io import fits, ascii
from shutil import copyfile
from glob import glob
from pkg_resources import resource_filename
import numpy as np
import os

def convert_ATLAS9(filepath, destination='', template=resource_filename('ExoCTK', 'data/ModelGrid_tmp.fits')):
    """
    Split ATLAS9 FITS files into separate files containing one Teff, log(g), and Fe/H
    
    Parameters
    ----------
    filepath: str
        The path to the ATLAS9 FITS file to convert
    destination: str
        The destination for the split files
    template: str
        The path to the FITS template file to use
    """        
    # Get all the data
    L = open('/Users/jfilippazzo/Desktop/im25k2new.pck').readlines()
    
    # Get the indexes of each log(g) chunk
    start = []
    for idx,l in enumerate(L):
        if l.startswith('EFF'):
            start.append(idx)
    
    # Break up into chunks
    for n,idx in enumerate(start):
        
        # Get the parameters
        h = L[idx].strip()
        teff = int(h.split()[1].split('.')[0])
        logg = float(h.split()[3][:3])
        feh = 0.
        logg_txt = str(abs(int(logg*10.))).zfill(2)
        feh_txt = '{}{}'.format('m' if feh<0 else 'p', str(abs(int(feh*10.))).zfill(2))
        
        # Parse the data
        try:
            end = start[n+1]
        except:
            end = -1
        data = L[idx+3:end-4]
        
        # Fix column spacing
        for n,l in enumerate(data):
            data[n] = l[:18]+' '+' '.join([l[idx:idx+6] for idx in np.arange(18,len(l),6)])
            
        cols = ['wl']+L[idx+2].strip().split()
        data = ascii.read(data, names=cols)

        # Put intensity array for each mu value in a cube
        data_cube = np.array([data[cols][n] for n in cols[1:]])[::-1]

        # mu values
        mu = list(map(float,cols[1:]))[::-1]

        # Get the wavelength and convert from nm to A
        wave = np.array(data['wl'])*10.

        # Copy the old HDU list
        new_file = destination+'ATLAS9_{}_{}_{}.fits'.format(teff,logg_txt,feh_txt)
        HDU = fits.open(template)
        
        # Write the new data
        HDU[0].data = data_cube
        HDU[1].data = mu
        HDU[0].header['PHXTEFF'] = teff
        HDU[0].header['PHXLOGG'] = logg
        HDU[0].header['PHXM_H'] = feh

        # Create a WAVELENGTH extension
        ext = fits.ImageHDU(wave)
        ext.update_ext_name('WAVELENGTH')
        HDU.append(ext)
        
        # Write the new file
        fits.HDUList(HDU).writeto(new_file, clobber=True)
        
        HDU.close()