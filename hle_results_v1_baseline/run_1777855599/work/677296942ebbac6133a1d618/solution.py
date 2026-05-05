import numpy as np

def solve_schrodinger(alpha, N=1000, x_max=10.0):
    x = np.linspace(-x_max, x_max, N)
    dx = x[1] - x[0]
    
    V = 0.5 * x**6 - alpha * x**4 + 0.5 * (alpha**2 - 7) * x**2
    
    diag = 1.0 / dx**2 + V
    off_diag = -0.5 / (2.0 * dx**2) * np.ones(N-1)
    
    H = np.zeros((N, N))
    for i in range(N):
        H[i, i] = diag[i]
        if i > 0:
            H[i, i-1] = off_diag[i-1]
        if i < N-1:
            H[i, i+1] = off_diag[i]
    
    eigenvalues, eigenvectors = np.linalg.eigh(H)
    
    E0 = eigenvalues[0]
    E2 = eigenvalues[2]
    
    psi0 = eigenvectors[:, 0]
    psi2 = eigenvectors[:, 2]
    
    psi0_0 = np.interp(0.0, x, psi0)
    psi0_alpha = np.interp(alpha, x, psi0)
    psi2_0 = np.interp(0.0, x, psi2)
    psi2_alpha = np.interp(alpha, x, psi2)
    
    F = (E2 / E0) * (psi2_alpha / psi2_0) / (psi0_alpha / psi0_0)
    
    return F

# Search for root between alpha=1.0 and alpha=2.0
# and between alpha=4.0 and alpha=5.0
def find_root(a1, a2):
    f1 = solve_schrodinger(a1)
    f2 = solve_schrodinger(a2)
    if f1 * f2 > 0:
        return None
    
    # Bisection method
    low = a1
    high = a2
    for _ in range(50):
        mid = (low + high) / 2
        f_mid = solve_schrodinger(mid)
        if f_mid == 0:
            return mid
        if f1 * f_mid < 0:
            high = mid
        else:
            low = mid
            f1 = f_mid
    return (low + high) / 2

print(f"Root between 1 and 2: {find_root(1.0, 2.0)}")
print(f"Root between 4 and 5: {find_root(4.0, 5.0)}")

