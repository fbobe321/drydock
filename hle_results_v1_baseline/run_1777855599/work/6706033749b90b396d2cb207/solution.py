import numpy as np

def solve():
    alpha = 100
    beta = 20

    def get_equilibrium(n):
        low = 0.0
        high = 200.0
        for _ in range(100):
            mid = (low + high) / 2
            val = alpha / (1 + mid**n) - mid - beta / (1 + mid)
            if val > 0:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    def check_stability(n):
        s = get_equilibrium(n)
        # Jacobian J = [[A, 0, B],
        #              [B, A, 0],
        #              [0, B, A]]
        # A = df/dx = -1 + beta/(1+s)^2
        # B = df/dz = -alpha * n * s^(n-1) / (1+s^n)^2
        # Eigenvalues: L = A + B * omega, where omega^3 = 1
        # L1 = A + B
        # L2,3 = A + B * (-1/2 +/- i*sqrt(3)/2) = (A - B/2) +/- i*B*sqrt(3)/2
        # Stability requires Re(L) < 0 for all L.
        # Since B is negative, let K = -B (K > 0).
        # L1 = A - K
        # L2,3 = (A + K/2) +/- i*K*sqrt(3)/2
        # Stability: A - K < 0 AND A + K/2 < 0.
        # Since K > 0, A + K/2 < 0 is the stricter condition.
        # Oscillations occur when A + K/2 > 0.
        
        A = -1 + beta / (1 + s)**2
        B = -alpha * n * (s**(n-1)) / (1 + s**n)**2
        K = -B
        
        # Stability condition: A + K/2 < 0
        return (A + K/2) < 0

    print("Testing n values:")
    for n in range(1, 21):
        is_stable = check_stability(float(n))
        print(f"n={n}: {'Stable' if is_stable else 'Oscillates'}")

if __name__ == "__main__":
    solve()
