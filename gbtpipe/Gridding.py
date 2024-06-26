#from sdpy import makecube
import numpy as np
import glob
from astropy.io import fits
import astropy.wcs as wcs
import itertools
from scipy.special import j1
import pdb
import numpy.fft as fft
import astropy.utils.console as console
import astropy.units as u
import astropy.constants as con
import numpy.polynomial.legendre as legendre
import warnings
from .Baseline import *
import os
from spectral_cube import SpectralCube
from radio_beam import Beam
from astropy.coordinates import SkyCoord
import matplotlib.pyplot as plt
from .Preprocess import preprocess

from . import __version__

# It's going to happen.  We should get used to it.
np.seterr(divide='ignore', invalid='ignore')

def is_outlier(points, thresh=3.5):
    """
    Pulled from:
    https://github.com/joferkington/oost_paper_code/blob/master/utilities.py

    Returns a boolean array with True if points are outliers and False 
    otherwise.

    Parameters:
    -----------
        points : An numobservations by numdimensions array of observations
        thresh : The modified z-score to use as a threshold. Observations with
            a modified z-score (based on the median absolute deviation) greater
            than this value will be classified as outliers.

    Returns:
    --------
        mask : A numobservations-length boolean array.

    References:
    ----------
        Boris Iglewicz and David Hoaglin (1993), "Volume 16: How to Detect and
        Handle Outliers", The ASQC Basic References in Quality Control:
        Statistical Techniques, Edward F. Mykytka, Ph.D., Editor. 
    """
    if len(points.shape) == 1:
        points = points[:,None]
    median = np.median(points, axis=0)
    diff = np.sum((points - median)**2, axis=-1)
    diff = np.sqrt(diff)
    med_abs_deviation = np.median(diff)

    modified_z_score = 0.6745 * diff / med_abs_deviation

    return modified_z_score > thresh

def postConvolve(filein, bmaj=None, bmin=None, 
                 bpa=0 * u.deg, beamscale=1.1,
                 fileout=None):

    """
    This is a cube convolution wrapper that increases the beam size
    modestly to improve sensitivity.

    filein : str
        Name of the FITS file to convolve

    bmaj ; astropy.Quantity.Angle
        Size of the new beam major axis

    bmin ; astropy.Quantity.Angle
        Size of the new beam minor axis
    
    bpa ; astropy.Quantity.Angle
        Position angle of new beam

    beamscale : float
       Increase the beam size by this fraction upon convolution.
       Default to 1.1
       
    fileout : str
       Name of file to write out.  Defaults to appending '_conv' to
       filename

    """
    if '.fits' not in filein:
        filein += '.fits'
    cube = SpectralCube.read(filein)

    if bmaj is None:
        bmaj = cube.beam.major * beamscale
    if bmin is None:
        bmin = bmaj
    if fileout is None:
        fileout = filein.replace('.fits','_conv.fits')

    targetBeam = Beam(bmaj, bmin, bpa)
    newcube = cube.convolve_to(targetBeam)
    newcube.write(fileout, overwrite=True)
    
def mad1d(x):
    med0 = np.median(x)
    return np.median(np.abs(x - med0)) * 1.4826


def jincGrid(xpix, ypix, xdata, ydata, pixPerBeam):
    a = 1.55 / (3.0 / pixPerBeam)
    b = 2.52 / (3.0 / pixPerBeam)

    Rsup = 1.09 * pixPerBeam  # Support radius is ~1 FWHM (Leroy likes 1.09)
    dmin = 1e-4
    dx = (xdata - xpix)
    dy = (ydata - ypix)

    pia = np.pi / a
    b2 = 1. / (b**2)
    distance = np.sqrt(dx**2 + dy**2)

    ind  = (np.where(distance <= Rsup))
    d = distance[ind]
    wt = j1(d * pia) / \
        (d * pia) * \
        np.exp(-d**2 * b2)
    wt[(d < dmin)] = 0.5  # Peak of the jinc function is 0.5 not 1.0

    return(wt, ind)

def gaussGrid(xpix, ypix, xdata, ydata, pixPerBeam):
    b = 1.00 / (3.0 / pixPerBeam)
    Rsup = 1.00 * pixPerBeam  # Support radius is ~1 FWHM (Leroy likes 1.09)
    dx = (xdata - xpix)
    dy = (ydata - ypix)
    b2 = 1. / (b**2)
    distance = np.sqrt(dx**2 + dy**2)
    ind  = (np.where(distance <= Rsup))
    d = distance[ind]
    wt = np.exp(-d**2 * b2)
    return(wt, ind)


def autoHeader(filelist, beamSize=0.0087, pixPerBeam=3.0,
               projection='TAN', discardSky=True):
    RAlist = []
    DEClist = []
    for thisfile in filelist: 
        s = fits.getdata(thisfile)
        if len(s) > 0:
            try:
                idx = (s['OBJECT'] != 'VANE') * (s['OBJECT'] != 'SKY')
                RAlist = RAlist + [s['CRVAL2'][idx]]
                DEClist = DEClist + [s['CRVAL3'][idx]]
            except:
                pdb.set_trace()

    longitude = np.array(list(itertools.chain(*RAlist)))
    latitude = np.array(list(itertools.chain(*DEClist)))
    longitude = longitude[longitude != 0]
    latitude = latitude[latitude != 0]
    minLon = np.nanmin(longitude)
    maxLon = np.nanmax(longitude)
    minLat = np.nanmin(latitude)
    maxLat = np.nanmax(latitude)
    
    naxis2 = np.ceil((maxLat - minLat) /
                     (beamSize / pixPerBeam) + 2 * pixPerBeam)
    crpix2 = naxis2 / 2
    cdelt2 = beamSize / pixPerBeam
    crval2 = (maxLat + minLat) / 2
    ctype2 = s[0]['CTYPE3'] 
    ctype2 += '-'*(5-len(ctype2))+projection
    # Negative to go in the usual direction on sky:
    cdelt1 = -beamSize / pixPerBeam

    naxis1 = np.ceil((maxLon - minLon) /
                     (beamSize / pixPerBeam) *
                     np.cos(crval2 / 180 * np.pi) + 2 * pixPerBeam)
    crpix1 = naxis1 / 2
    crval1 = (minLon + maxLon) / 2
    ctype1 = s[0]['CTYPE2'] 
    ctype1 += '-'*(5-len(ctype1))+projection
    outdict = {'CRVAL1': crval1, 'CRPIX1': crpix1,
               'CDELT1': cdelt1, 'NAXIS1': naxis1,
               'CTYPE1': ctype1, 'CRVAL2': crval2,
               'CRPIX2': crpix2, 'CDELT2': cdelt2,
               'NAXIS2': naxis2, 'CTYPE2': ctype2}
    
    return(outdict)


def addHeader_nonStd(hdr, beamSize, sample):

    unique_units, posn = np.unique(sample['TUNIT7'],
                                   return_inverse=True)
    counts = np.bincount(posn)
    bunit = unique_units[counts.argmax()]

    unique_frontend, posn = np.unique(sample['FRONTEND'],
                                      return_inverse=True)
    counts = np.bincount(posn)
    frontend = unique_frontend[counts.argmax()]

    bunit_dict = {'Tmb':'K',
                  'Ta*':'K',
                  'Ta':'K',
                  'Counts':'Counts'}
    inst_dict = {'RcvrArray75_115':'ARGUS',
                 'RcvrArray18_26':'KFPA',
                 'Rcvr1_2':'L-BAND'}
    hdr.set('BUNIT', value= bunit_dict[bunit], 
            comment=bunit)
    hdr['INSTRUME'] = inst_dict[frontend]
    hdr['BMAJ'] = beamSize
    hdr['BMIN'] = beamSize
    hdr['BPA'] = 0.0
    hdr['TELESCOP'] = 'GBT'

    return(hdr)

def griddata(filelist, 
             pixPerBeam=3.5,
             templateHeader=None,
             gridFunction=jincGrid,
             startChannel=None, endChannel=None,
             rebase=None,
             rebaseorder=None,
             beamSize=None,
             flagSpatialOutlier=False,
             projection='TAN',
             outdir=None, 
             outname=None,
             dtype=np.float64,
             gainDict=None,
             **kwargs):

    """Gridding code for GBT spectral scan data produced by pipeline.
    
    Parameters
    ----------
    filelist : list
        List of FITS files to be gridded into an output

    Keywords
    --------
    pixPerBeam : float
        Number of pixels per beam FWHM

    templateHeader : `Header` object
        Template header used for spatial pixel grid.

    gridFunction : function 
        Gridding function to be used.  The default `jincGrid` is a
        tapered circular Bessel function.  The function has call
        signature of func(xPixelCentre, yPixelCenter, xData, yData,
        pixPerBeam)

    startChannel : int
        Starting channel for spectrum within the original spectral data.

    endChannel : int
        End channel for spectrum within the original spectral data

    outdir : str
        Output directory name.  Defaults to current working directory.

    outname : str
        Output directory file name.  Defaults to object name in the
        original spectra.

    flagSpatialOutlier : bool
        Setting to True will remove scans with positions far outside the 
        bounding box of the regular scan pattern. Used to catch instances
        where the encoder records erroneous positions. 

    gainDict : dict 
        Dictionary that has a tuple of feed and polarization numbers
        as keys and returns the gain values for that feed.
    
    Returns
    -------
    None

    """

    eulerFlag = False
    print("Starting Gridding")
    if outdir is None:
        outdir = os.getcwd()

    if len(filelist) == 0:
        warnings.warn('There are no FITS files to process ')
        return
    # check that every file in the filelist is valid
    # If not then remove it and send warning message
    for file_i in filelist:
        try:
            fits.open(file_i)
        except:
            warnings.warn('file {0} is corrupted'.format(file_i))
            filelist.remove(file_i)

    # pull a test structure
    hdulist = fits.open(filelist[0])
    s = hdulist[1].data
    
    # Constants block
    sqrt2 = np.sqrt(2)
    mad2rms = 1.4826
    prefac = mad2rms / sqrt2
    c = 299792458.

    nu0 = s[0]['RESTFREQ']
    Data_Unit = s[0]['TUNIT7']

    if outname is None:
        outname = s[0]['OBJECT']

    # New Beam size measurements use 1.18 vs. 1.22 based on GBT Memo 296.
    
    if beamSize is None:
        beamSize = 1.18 * (c / nu0 / 100.0) * 180 / np.pi  # in degrees

    if startChannel is None:
        startChannel = 0
    if endChannel is None:
        endChannel = len(s[0]['DATA'])
    
    naxis3 = len(s[0]['DATA'][startChannel:endChannel])

    # Default behavior is to park the object velocity at
    # the center channel in the VRAD-LSR frame

    crval3 = s[0]['RESTFREQ'] * (1 - s[0]['VELOCITY'] / c)
    crpix3 = s[0]['CRPIX1'] - startChannel
    ctype3 = s[0]['CTYPE1']
    cdelt3 = s[0]['CDELT1']

    w = wcs.WCS(naxis=3)

    w.wcs.restfrq = nu0
    # We are forcing this conversion to make nice cubes.
    w.wcs.specsys = 'LSRK'
    w.wcs.ssysobs = 'TOPOCENT'

    if templateHeader is None:
        wcsdict = autoHeader(filelist, beamSize=beamSize,
                             pixPerBeam=pixPerBeam, projection=projection)
        w.wcs.crpix = [wcsdict['CRPIX1'], wcsdict['CRPIX2'], crpix3]
        w.wcs.cdelt = np.array([wcsdict['CDELT1'], wcsdict['CDELT2'], cdelt3])
        w.wcs.crval = [wcsdict['CRVAL1'], wcsdict['CRVAL2'], crval3]
        w.wcs.ctype = [wcsdict['CTYPE1'], wcsdict['CTYPE2'], ctype3]
        naxis2 = wcsdict['NAXIS2']
        naxis1 = wcsdict['NAXIS1']
        w.wcs.radesys = s[0]['RADESYS']
        w.wcs.equinox = s[0]['EQUINOX']

    else:
        w.wcs.crpix = [templateHeader['CRPIX1'],
                       templateHeader['CRPIX2'], crpix3]
        w.wcs.cdelt = np.array([templateHeader['CDELT1'],
                                templateHeader['CDELT2'], cdelt3])
        w.wcs.crval = [templateHeader['CRVAL1'],
                       templateHeader['CRVAL2'], crval3]
        w.wcs.ctype = [templateHeader['CTYPE1'],
                       templateHeader['CTYPE2'], ctype3]
        naxis2 = templateHeader['NAXIS2']
        naxis1 = templateHeader['NAXIS1']
        w.wcs.radesys = templateHeader['RADESYS']
        w.wcs.equinox = templateHeader['EQUINOX']
        pixPerBeam = np.abs(beamSize / w.pixel_scale_matrix[1,1])
        if pixPerBeam < 3.5:
            warnings.warn('Template header requests {0}'.format(pixPerBeam)+
                          ' pixels per beam.')
        if (((w.wcs.ctype[0]).split('-'))[0] !=
            ((s[0]['CTYPE1']).split('-'))[0]):
            warnings.warn('Spectral data not in same frame as template header')
            eulerFlag = True

    outCube = np.zeros((int(naxis3), int(naxis2), int(naxis1)),dtype=dtype)
    outWts = np.zeros((int(naxis2), int(naxis1)),dtype=dtype)

    xmat, ymat = np.meshgrid(np.arange(naxis1), np.arange(naxis2),
                             indexing='ij')
    xmat = xmat.reshape(xmat.size)
    ymat = ymat.reshape(ymat.size)
    xmat = xmat.astype(int)
    ymat = ymat.astype(int)

    ctr = 0

    for thisfile in filelist:
        print("Now processing {0}".format(thisfile))
        print("This is file {0} of {1}".format(ctr, len(filelist)))

        ctr += 1
        s = fits.open(thisfile)

        if len(s) < 2:
            warnings.warn("Corrupted file: {0}".format(thisfile))
            continue

            
        if len(s[1].data) == 0:
            warnings.warn("Corrupted file: {0}".format(thisfile))
            continue
        
        if flagSpatialOutlier:
                # Remove outliers in Lat/Lon space
                f = np.where(is_outlier(s[1].data['CRVAL2'], thresh=1.5)!=True)
                s[1].data = s[1].data[f]
                f = np.where(is_outlier(s[1].data['CRVAL3'], thresh=1.5)!=True)
                s[1].data = s[1].data[f]
        nuindex = np.arange(len(s[1].data['DATA'][0]))

        flagct = 0
        if eulerFlag:
            if 'GLON' in s[1].data['CTYPE2'][0]:
                inframe = 'galactic'
            elif 'RA' in s[1].data['CTYPE2'][0]:
                inframe = 'fk5'
            else:
                raise NotImplementedError
            if 'GLON' in w.wcs.ctype[0]:
                outframe = 'galactic'
            elif 'RA' in w.wcs.ctype[0]:
                outframe = 'fk5'
            else:
                raise NotImplementedError
            
            coords = SkyCoord(s[1].data['CRVAL2'],
                              s[1].data['CRVAL3'],
                              unit = (u.deg, u.deg),
                              frame=inframe)
            coords_xform = coords.transform_to(outframe)
            if outframe == 'fk5':
                longCoord = coords_xform.ra.deg
                latCoord = coords_xform.dec.deg
            elif outframe == 'galactic':
                longCoord = coords_xform.l.deg
                latCoord = coords_xform.b.deg
        else:
            longCoord = s[1].data['CRVAL2']
            latCoord = s[1].data['CRVAL3']

        spectra, outscan, specwts, tsys = preprocess(thisfile,
                                                     startChannel=startChannel,
                                                     endChannel=endChannel,
                                                     **kwargs)
        for i in range(len(spectra)):    
            xpoints, ypoints, zpoints = w.wcs_world2pix(longCoord[i],
                                                        latCoord[i],
                                                        spectra[i]['CRVAL1'], 0)
            if (tsys[i] > 10) and (xpoints > 0) and (xpoints < naxis1) \
                    and (ypoints > 0) and (ypoints < naxis2):
                pixelWeight, Index = gridFunction(xmat, ymat,
                                                xpoints, ypoints,
                                                pixPerBeam)
                vector = np.outer(outscan[i, :] * specwts[i, :],
                                    pixelWeight / tsys[i]**2)
                wts = pixelWeight / tsys[i]**2
                outCube[:, ymat[Index], xmat[Index]] += vector
                outWts[ymat[Index], xmat[Index]] += wts
        # Temporarily do a file write for every batch of scans.
        outWtsTemp = np.copy(outWts)
        outWtsTemp.shape = (1,) + outWtsTemp.shape
        outCubeTemp = np.copy(outCube)
        outCubeTemp /= outWtsTemp
        hdr = fits.Header(w.to_header())
        
        hdr = addHeader_nonStd(hdr, beamSize, s[1].data)
        #
        hdu = fits.PrimaryHDU(outCubeTemp, header=hdr)
        hdu.writeto(outdir + '/' + outname + '.fits', overwrite=True)
        
    outWts.shape = (1,) + outWts.shape
    outCube /= outWts

    # Create basic fits header from WCS structure
    hdr = fits.Header(w.to_header())
    # Add non standard fits keyword
    hdr = addHeader_nonStd(hdr, beamSize, s[1].data[0])
    hdr.add_history('Using GBTPIPE gridder version {0}'.format(__version__))
    hdu = fits.PrimaryHDU(outCube, header=hdr)
    hdu.writeto(outdir + '/' + outname + '.fits', overwrite=True)

    w2 = w.dropaxis(2)
    hdr2 = fits.Header(w2.to_header())
    hdu2 = fits.PrimaryHDU(outWts, header=hdr2)
    hdu2.writeto(outdir + '/' + outname + '_wts.fits', overwrite=True)

    # if rebase:
    #     if rebaseorder is None:
    #         rebaseorder = blorder
    #     if 'NH3_11' in outname:
    #         rebaseline(outdir + '/' + outname + '.fits',
    #                    windowFunction=Baseline.ammoniaWindow,
    #                    line='oneone', blorder=rebaseorder,
    #                    **kwargs)

    #     elif 'NH3_22' in outname:
    #         rebaseline(outdir + '/' + outname + '.fits',
    #                    windowFunction=Baseline.ammoniaWindow,
    #                    line='twotwo', blorder=rebaseorder,
    #                    **kwargs)

    #     elif 'NH3_33' in outname:
    #         rebaseline(outdir + '/' + outname + '.fits',
    #                    winfunc = Baseline.ammoniaWindow,
    #                    blorder=rebaseorder,
    #                    line='threethree', **kwargs)
    #     else:
    #         rebaseline(outdir + '/' + outname + '.fits',
    #                    blorder=rebaseorder,
    #                    windowFunction=Baseline.tightWindow, 
    #                    **kwargs)


        # for idx, spectrum in enumerate(console.ProgressBar((s[1].data))):
        # for idx, spectrum in enumerate((s[1].data)):
        # # Generate Baseline regions
        #     baselineIndex = np.concatenate([nuindex[ss]
        #                                     for ss in baselineRegion])

        #     specData = spectrum['DATA']
        #     if gainDict:
        #         try:
        #             feedwt = 1.0/gainDict[(str(spectrum['FDNUM']).strip(),
        #                                    str(spectrum['PLNUM']).strip())]
        #         except KeyError:
        #             continue
        #     else:
        #         feedwt = 1.0
        #     if spectrum['OBJECT'] == 'VANE' or spectrum['OBJECT'] == 'SKY':
        #         continue
        #     # baseline fit
        #     if flagSpike:
        #         jumps = (specData - np.roll(specData, -1))
        #         noise = mad1d(jumps) * 2**(-0.5)
        #         spikemask = (np.abs(jumps) < spikeThresh * noise)
        #         spikemask = spikemask * np.roll(spikemask, 1)
        #         specData[~spikemask] = 0.0
        #     else:
        #         spikemask = np.ones_like(specData, dtype=bool)

        #     if doBaseline & np.all(np.isfinite(specData[baselineIndex])):
        #         specData = baselineSpectrum(specData, order=blorder,
        #                                     baselineIndex=baselineIndex)

        #     # This part takes the TOPOCENTRIC frequency that is at
        #     # CRPIX1 (i.e., CRVAL1) and calculates the what frequency
        #     # that would have in the LSRK frame with freqShiftValue.
        #     # This then compares to the desired frequency CRVAL3.

        #     DeltaNu = freqShiftValue(spectrum['CRVAL1'],
        #                              -vframe[idx]) - crval3

        #     DeltaChan = DeltaNu / cdelt3
        #     specData = channelShift(specData, -DeltaChan)

        #     outslice = (specData)[startChannel:endChannel]
        #     spectrum_wt = ((np.isfinite(outslice)
        #                     * spikemask[startChannel:
        #                                 endChannel]).astype(float)
        #                    * feedwt)
        #     outslice = np.nan_to_num(outslice)
        #     xpoints, ypoints, zpoints = w.wcs_world2pix(longCoord[idx],
        #                                                 latCoord[idx],
        #                                                 spectrum['CRVAL1'], 0)
        #     tsys = spectrum['TSYS']

        #     if flagRMS:
        #         radiometer_rms = tsys / np.sqrt(np.abs(spectrum['CDELT1']) *
        #                                         spectrum['EXPOSURE'])
        #         scan_rms = prefac * np.median(np.abs(outslice[0:-2] -
        #                                                 outslice[2:]))

        #         if scan_rms > rmsThresh * radiometer_rms:
        #             tsys = 0 # Blank spectrum

        #     if flagRipple:
        #         scan_rms = prefac * np.median(np.abs(outslice[0:-2] -
        #                                              outslice[2:]))
        #         ripple = prefac * sqrt2 * np.median(np.abs(outslice))

        #         if ripple > rippleThresh * scan_rms:
        #             tsys = 0 # Blank spectrum
        #     if tsys == 0:
        #         flagct +=1
    # print ("Percentage of flagged scans: {0:4.2f}".format(
    #         100*flagct/float(idx)))


    #         if (tsys > 10) and (xpoints > 0) and (xpoints < naxis1) \
    #                 and (ypoints > 0) and (ypoints < naxis2):
    #             if plotTimeSeries:
    #                 outscans[idx, startChannel:endChannel] = outslice
    #             pixelWeight, Index = gridFunction(xmat, ymat,
    #                                               xpoints, ypoints,
    #                                               pixPerBeam)
    #             vector = np.outer(outslice * spectrum_wt,
    #                               pixelWeight / tsys**2)
    #             wts = pixelWeight / tsys**2
    #             outCube[:, ymat[Index], xmat[Index]] += vector
    #             outWts[ymat[Index], xmat[Index]] += wts
    #     print ("Percentage of flagged scans: {0:4.2f}".format(
    #             100*flagct/float(idx)))
        # if not OnlineDoppler:
        #     vframe = VframeInterpolator(s[1].data)
        # else:
        #     vframe = s[1].data['VFRAME']
