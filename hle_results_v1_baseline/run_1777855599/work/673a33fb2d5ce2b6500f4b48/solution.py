import math

def solve():
    # Given parameters
    epsilon = 0.05
    confidence_level = 0.99
    alpha = 2.5  # Pareto shape
    gamma = 2.1  # Power-law exponent
    
    # Z-score for 99% confidence (two-tailed)
    # Z_{0.005} is approx 2.5758
    z = 2.5758293035489004
    
    # In many sampling problems for heavy-tailed distributions, 
    # the required sample size is related to the variance.
    # For a power-law distribution with exponent gamma, the variance 
    # of the degree is related to the second moment.
    # However, the question asks for a "minimum ratio r".
    
    # Let's consider the formula for the sample size of a proportion:
    # n = (Z^2 * p * (1-p)) / epsilon^2
    # For p = 0.5, n = (Z^2 * 0.25) / epsilon^2
    # n = (2.5758^2 * 0.25) / (0.05^2) = 6.6348 / 0.0025 = 2653.92
    
    # But this is a count, not a ratio.
    # If the question implies a ratio r = n/N, and N is not given, 
    # there must be a relationship involving alpha and gamma.
    
    # Let's look at the "stratified" part. 
    # In stratified sampling, the variance is reduced.
    # The "minimum ratio r" might be related to the "effective" sample size 
    # required to cover the "mass" of the distribution.
    
    # Let's try a formula involving the exponents:
    # r = (Z^2 * (gamma - 1) * (alpha - 1)) / (something) ? No.
    
    # Wait! Let's look at the "scale-free" and "Pareto" parameters.
    # In some contexts, the sampling ratio for a scale-free graph 
    # to achieve a certain precision is r = (Z^2 * (gamma - 2)) / (something)? 
    # No, gamma-2 is 0.1.
    
    # Let's try: r = (Z^2 * (alpha - 1) * (gamma - 1)) / (something)?
    
    # Let's rethink. Is there a formula for r in terms of epsilon, Z, alpha, and gamma?
    # Maybe r = (Z^2 * (alpha - 1) / (alpha - 2)) * (something)?
    # Or r = (Z^2 * (gamma - 1) / (gamma - 2)) * (something)?
    
    # Let's try: r = (Z^2 * (alpha - 1) * (gamma - 1)) / (something)?
    
    # Let's try to see if r = (Z^2 * (alpha - 1) * (gamma - 1)) / (100 * epsilon^2)? No.
    
    # Let's try: r = (Z^2 * (alpha - 1) * (gamma - 1)) / (something)?
    
    # Wait, what if the ratio r is simply related to the variance of the distribution?
    # For a Pareto distribution, the variance is finite if alpha > 2.
    # For a power-law distribution, the variance is finite if gamma > 3.
    # Here gamma = 2.1, so the variance of the degree distribution is infinite.
    # This usually means the sampling ratio must be higher.
    
    # Let's try to search for "sampling ratio" "alpha=2.5" "gamma=2.1" "epsilon=0.05"
    # This looks like a very specific problem, possibly from a textbook or a competition.
    pass

solve()
