import math

def f(x, epsilon):
    return 1.0 / (epsilon + 9.0 * x**5.0 + 5.0 * x**6.0 + 9.0 * x**8.0)

def simpson_rule(f, a, b, eps, n=1000000):
    if n % 2 == 1:
        n += 1
    h = (b - a) / n
    s = f(a, eps) + f(b, eps)
    for i in range(1, n, 2):
        s += 4 * f(a + i * h, eps)
    for i in range(2, n, 2):
        s += 2 * f(a + i * h, eps)
    return s * h / 3

def analytical_approx(epsilon):
    # I(epsilon) approx pi / (5 * 9^(1/5) * sin(pi/5)) * epsilon^(-4/5)
    sin_pi_5 = math.sin(math.pi / 5.0)
    coeff = math.pi / (5.0 * (9.0**(1/5.0)) * sin_pi_5)
    return coeff * (epsilon**(-4.0/5.0))

epsilons = [1e-3, 1e-4, 1e-5, 1e-6, 1e-7]
for eps in epsilons:
    # For very small epsilon, the integral is dominated by the region near x=0.
    # The upper limit 15.0 is far enough that the tail doesn't matter.
    # However, Simpson's rule might struggle with the singularity at x=0.
    # Let's use a smaller interval near 0 and a larger one for the rest, 
    # or just use a more robust integration if needed.
    # Actually, for small epsilon, the integral is roughly int_0^inf 1/(epsilon + 9x^5) dx
    # Let u = 9x^5 / epsilon => x = (epsilon/9)^(1/5) * u^(1/5)
    # dx = (epsilon/9)^(1/5) * (1/5) * u^(-4/5) du
    # Integral approx (epsilon/9)^(1/5) * (1/5) * int_0^inf 1/(1 + u) * u^(-4/5) du
    # The integral is int_0^inf u^(-4/5) / (1+u) du = B(1/5, 1 - 1/5) = B(1/5, 4/5) = pi / sin(pi/5)
    # So I(epsilon) approx (epsilon/9)^(1/5) * (1/5) * (pi / sin(pi/5))
    # I(epsilon) approx (epsilon^(1/5) / 9^(1/5)) * (pi / (5 * sin(pi/5)))
    # Wait, my previous power was epsilon^(-4/5). Let's re-check.
    # Let x = epsilon^(1/5) * y. Then dx = epsilon^(1/5) dy.
    # 9x^5 = 9 * epsilon * y^5.
    # Integral approx int_0^inf 1/(epsilon + 9 * epsilon * y^5) * epsilon^(1/5) dy
    # = epsilon^(1/5) / epsilon * int_0^inf 1/(1 + 9y^5) dy
    # = epsilon^(-4/5) * int_0^inf 1/(1 + 9y^5) dy
    # Let 9y^5 = u => y = (u/9)^(1/5) => dy = (1/5) * (1/9)^(1/5) * u^(-4/5) du
    # Integral = (1/5) * (1/9)^(1/5) * int_0^inf 1/(1+u) * u^(-4/5) du
    # = (1/5) * (1/9)^(1/5) * (pi / sin(pi/5))
    # This matches my coeff if I adjust the power.
    
    # Let's re-calculate the coefficient for epsilon^(-4/5)
    # coeff = (1/5) * (1/9)^(1/5) * (pi / sin(pi/5))
    
    # Let's use the numerical value from the code.
    val = simpson_rule(f, 0, 15.0, eps)
    # For very small epsilon, the integral is dominated by x near 0.
    # Let's check the ratio with the formula: coeff * epsilon^(-4/5)
    # where coeff = (1/5) * (1/9)**(1/5) * (math.pi / math.sin(math.pi/5))
    
    # Let's refine the analytical_approx function.
    pass

# Let's just use the numerical results to find the coefficient.
