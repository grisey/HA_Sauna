"""Student-t tail probabilities without an optional numerical dependency.

The incomplete-beta continued fraction is DLMF 8.17.22 (8.17.4 for
symmetry). Tolerances concern floating-point convergence, never recognition.
Failure to converge raises; it cannot become negative event evidence.
"""

from math import copysign, exp, expm1, isfinite, lgamma, log, log1p
from sys import float_info


def _log_complement(log_probability):
    if log_probability > 0:
        raise ArithmeticError("A log probability cannot exceed zero")
    return (log(-expm1(log_probability)) if log_probability > -log(2)
            else log1p(-exp(log_probability)))


def _beta_fraction(a, b, x):
    tiny = float_info.min / float_info.epsilon

    def nonzero(value):
        return value if abs(value) >= tiny else copysign(tiny, value)

    c = 1.0
    d = 1 / nonzero(1 - (a + b) * x / (a + 1))
    result = d
    for m in range(1, 10001):
        # Two terms of the continued fraction per iteration.
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1 / nonzero(1 + numerator * d)
            c = nonzero(1 + numerator / c)
            delta = d * c
            result *= delta
        if abs(delta - 1) <= 8 * float_info.epsilon:
            if result <= 0 or not isfinite(result):
                raise ArithmeticError("Incomplete beta fraction is not representable")
            return result
    raise ArithmeticError("Incomplete beta fraction did not converge")


def _log_beta_lower(x, a, b):
    if x == 0:
        return float("-inf")
    if x == 1:
        return 0.0
    factor = (lgamma(a + b) - lgamma(a) - lgamma(b)
              + a * log(x) + b * log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return factor + log(_beta_fraction(a, b, x)) - log(a)
    complement = factor + log(_beta_fraction(b, a, 1 - x)) - log(b)
    return _log_complement(complement)


def student_log_survival(value, degrees):
    """log P(T > value), T a standard Student-t random variable."""
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not isfinite(value) or not isinstance(degrees, (int, float))
            or isinstance(degrees, bool) or not isfinite(degrees) or degrees <= 0):
        raise ValueError("Student-t needs a finite value and positive degrees")
    if value == 0:
        return -log(2)
    ratio = 2 * log(abs(value)) - log(degrees)
    if ratio < 0:
        # Around zero, 1-x remains representable when x rounds to one.
        log_z = ratio - log1p(exp(ratio))
        if log_z < log(float_info.min):
            central = (0.5 * log_z - log(0.5) + lgamma((degrees + 1) / 2)
                       - lgamma(0.5) - lgamma(degrees / 2))
        else:
            central = _log_beta_lower(exp(log_z), 0.5, degrees / 2)
        tail = _log_complement(central) - log(2)
        return tail if value > 0 else _log_complement(tail)
    # Keep log(x) even when x = nu/(nu+t²) would underflow.
    log_x = -(max(0.0, ratio) + log1p(exp(-abs(ratio))))
    if log_x < log(float_info.min):
        a = degrees / 2
        lower = (a * log_x - log(a)
                 + lgamma(a + 0.5) - lgamma(a) - lgamma(0.5))
    else:
        lower = _log_beta_lower(exp(log_x), degrees / 2, 0.5)
    tail = lower - log(2)
    return tail if value > 0 else _log_complement(tail)
