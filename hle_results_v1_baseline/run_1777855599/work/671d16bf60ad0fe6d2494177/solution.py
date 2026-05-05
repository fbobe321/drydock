import numpy as np

def solve_lambda(a, N):
    dx = 2 * np.pi / N
    a_mid = 0.5 * (a + np.roll(a, -1))
    
    S = np.zeros((N, N))
    for i in range(N):
        prev = (i - 1) % N
        nxt = (i + 1) % N
        S[i, i] = (a_mid[i] + a_mid[prev]) / dx
        S[i, nxt] = -a_mid[i] / dx
        S[i, prev] = -a_mid[prev] / dx
        
    M_vals = a * dx
    M_inv_sqrt = np.diag(1.0 / np.sqrt(M_vals))
    A = M_inv_sqrt @ S @ M_inv_sqrt
    
    # Use eigvals instead of eigh to avoid scipy dependency
    eigenvalues = np.linalg.eigvals(A)
    eigenvalues = np.sort(eigenvalues.real)
    return eigenvalues[1]

def test():
    N = 400
    # Case 1: a(x) = 1
    a1 = np.ones(N)
    lam1 = solve_lambda(a1, N)
    print(f"a=1, lambda: {lam1}")
    
    # Case 2: a(x) = 3 on [0, theta], 1 on [theta, 2pi]
    # We want to minimize lambda.
    best_lam = 1e10
    for i in range(1, N):
        theta = (i / N) * (2 * np.pi)
        a = np.ones(N)
        a[:i] = 3.0
        lam = solve_lambda(a, N)
        if lam < best_lam:
            best_lam = lam
    print(f"Min lambda found: {best_lam}")
    print(f"Max C found: {1.0/best_lam}")

test()
