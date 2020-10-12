from math import floor, ceil
from scipy import interpolate

from ._extensions._pywt import (DiscreteContinuousWavelet, ContinuousWavelet,
                                Wavelet, _check_dtype)
from ._functions import evaluate_wavelet, scale2frequency


__all__ = ["cwt"]


import numpy as np

try:
    # Prefer scipy.fft (new in SciPy 1.4)
    import scipy.fft
    fftmodule = scipy.fft
    next_fast_len = fftmodule.next_fast_len
except ImportError:
    try:
        import scipy.fftpack
        fftmodule = scipy.fftpack
        next_fast_len = fftmodule.next_fast_len
    except ImportError:
        fftmodule = np.fft

        # provide a fallback so scipy is an optional requirement
        def next_fast_len(n):
            """Round up size to the nearest power of two.

            Given a number of samples `n`, returns the next power of two
            following this number to take advantage of FFT speedup.
            This fallback is less efficient than `scipy.fftpack.next_fast_len`
            """
            return 2**ceil(np.log2(n))


def cwt(data, scales, wavelet, sampling_period=1., method='conv', axis=-1):
    """
    cwt(data, scales, wavelet)

    One dimensional Continuous Wavelet Transform.

    Parameters
    ----------
    data : array_like
        Input signal
    scales : array_like
        The wavelet scales to use. One can use
        ``f = scale2frequency(wavelet, scale)/sampling_period`` to determine
        what physical frequency, ``f``. Here, ``f`` is in hertz when the
        ``sampling_period`` is given in seconds.
    wavelet : Wavelet object or name
        Wavelet to use
    sampling_period : float
        Sampling period for the frequencies output (optional).
        The values computed for ``coefs`` are independent of the choice of
        ``sampling_period`` (i.e. ``scales`` is not scaled by the sampling
        period).
    method : {'conv', 'fft'}, optional
        The method used to compute the CWT. Can be any of:
            - ``conv`` uses ``numpy.convolve``.
            - ``fft`` uses frequency domain convolution.
            - ``auto`` uses automatic selection based on an estimate of the
              computational complexity at each scale.

        The ``conv`` method complexity is ``O(len(scale) * len(data))``.
        The ``fft`` method is ``O(N * log2(N))`` with
        ``N = len(scale) + len(data) - 1``. It is well suited for large size
        signals but slightly slower than ``conv`` on small ones.
    axis: int, optional
        Axis over which to compute the CWT. If not given, the last axis is
        used.

    Returns
    -------
    coefs : array_like
        Continuous wavelet transform of the input signal for the given scales
        and wavelet. The first axis of ``coefs`` corresponds to the scales.
        The remaining axes match the shape of ``data``.
    frequencies : array_like
        If the unit of sampling period are seconds and given, than frequencies
        are in hertz. Otherwise, a sampling period of 1 is assumed.

    Notes
    -----
    Size of coefficients arrays depends on the length of the input array and
    the length of given scales.

    Examples
    --------
    >>> import pywt
    >>> import numpy as np
    >>> import matplotlib.pyplot as plt
    >>> x = np.arange(512)
    >>> y = np.sin(2*np.pi*x/32)
    >>> coef, freqs=pywt.cwt(y,np.arange(1,129),'gaus1')
    >>> plt.matshow(coef) # doctest: +SKIP
    >>> plt.show() # doctest: +SKIP
    ----------
    >>> import pywt
    >>> import numpy as np
    >>> import matplotlib.pyplot as plt
    >>> t = np.linspace(-1, 1, 200, endpoint=False)
    >>> sig  = np.cos(2 * np.pi * 7 * t) + np.real(np.exp(-7*(t-0.4)**2)*np.exp(1j*2*np.pi*2*(t-0.4)))
    >>> widths = np.arange(1, 31)
    >>> cwtmatr, freqs = pywt.cwt(sig, widths, 'mexh')
    >>> plt.imshow(cwtmatr, extent=[-1, 1, 1, 31], cmap='PRGn', aspect='auto',
    ...            vmax=abs(cwtmatr).max(), vmin=-abs(cwtmatr).max())  # doctest: +SKIP
    >>> plt.show() # doctest: +SKIP
    """

    # accept array_like input; make a copy to ensure a contiguous array
    dt = _check_dtype(data)
    data = np.asarray(data, dtype=dt)
    dt_cplx = np.result_type(dt, np.complex64)
    if not isinstance(wavelet, (ContinuousWavelet, Wavelet)):
        wavelet = DiscreteContinuousWavelet(wavelet)
    if np.isscalar(scales):
        scales = np.array([scales])
    if not np.isscalar(axis):
        raise ValueError("axis must be a scalar.")

    dt_out = dt_cplx if wavelet.complex_cwt else dt
    out = np.empty((np.size(scales),) + data.shape, dtype=dt_out)
    precision = 10
    psi, x = evaluate_wavelet(wavelet, precision=precision)
    psi = np.conj(psi) if wavelet.complex_cwt else psi

    # convert psi, x to the same precision as the data
    dt_psi = dt_cplx if psi.dtype.kind == 'c' else dt
    psi = np.asarray(psi, dtype=dt_psi)
    x = np.asarray(x, dtype=data.real.dtype)
    # FIXME: The original wavelet function could be used here, but
    # interpolation is computationally more efficient.
    wavefun = interpolate.interp1d(x, psi, kind='cubic', assume_sorted=True)

    if method == 'fft':
        size_scale0 = -1
        fft_data = None
    elif not method == 'conv':
        raise ValueError("method must be 'conv' or 'fft'")

    if data.ndim > 1:
        # move axis to be transformed last (so it is contiguous)
        data = data.swapaxes(-1, axis)

        # reshape to (n_batch, data.shape[-1])
        data_shape_pre = data.shape
        data = data.reshape((-1, data.shape[-1]))

    for i, scale in enumerate(scales):
        # FIXME: Boundary points might be discarded erroneously
        if np.sign(x[0])*np.sign(x[-1])<0:
            # Wavelet is sampled at 0.0 if the range includes it
            xsl = np.arange(0.0, x[0], -1.0/scale)
            xsr = np.arange(0.0, x[-1], 1.0/scale)
            xs = np.concatenate((xsl[:0:-1], xsr))
        else:
            xs = np.arange(x[0], x[-1], 1.0/scale)
        psi_scale = wavefun(xs)[::-1]

        if method == 'conv':
            if data.ndim == 1:
                conv = np.convolve(data, psi_scale)
            else:
                # batch convolution via loop
                conv_shape = list(data.shape)
                conv_shape[-1] += psi_scale.size - 1
                conv_shape = tuple(conv_shape)
                conv = np.empty(conv_shape, dtype=dt_out)
                for n in range(data.shape[0]):
                    conv[n, :] = np.convolve(data[n], psi_scale)
        else:
            # The padding is selected for:
            # - optimal FFT complexity
            # - to be larger than the two signals length to avoid circular
            #   convolution
            size_scale = next_fast_len(
                data.shape[-1] + psi_scale.size - 1
            )
            if size_scale != size_scale0:
                # Must recompute fft_data when the padding size changes.
                fft_data = fftmodule.fft(data, size_scale, axis=-1)
            size_scale0 = size_scale
            fft_wav = fftmodule.fft(psi_scale, size_scale, axis=-1)
            conv = fftmodule.ifft(fft_wav * fft_data, axis=-1)
            conv = conv[..., :data.shape[-1] + psi_scale.size - 1]

        coef = conv / np.sqrt(scale)
        if out.dtype.kind != 'c':
            coef = coef.real
        # transform axis is always -1 due to the data reshape above
        d = (coef.shape[-1] - data.shape[-1]) / 2.
        if d > 0:
            coef = coef[..., floor(d):-ceil(d)]
        elif d < 0:
            raise ValueError(
                "Selected scale of {} too small.".format(scale))
        if data.ndim > 1:
            # restore original data shape and axis position
            coef = coef.reshape(data_shape_pre)
            coef = coef.swapaxes(axis, -1)
        out[i, ...] = coef

    frequencies = scale2frequency(wavelet, scales, precision)
    if np.isscalar(frequencies):
        frequencies = np.array([frequencies])
    frequencies /= sampling_period
    return out, frequencies
