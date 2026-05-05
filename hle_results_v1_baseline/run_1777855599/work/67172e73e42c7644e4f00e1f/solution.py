import numpy as np

def min_eigenvalue(n):
    A = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            A[i, j] = n - abs(i - j)
    eigenvalues = np.linalg.eigvals(A)
    return np.min(eigenvalues)

for n in [1000, 2000, 3000, 4000, 5000]:
    print(f"n={n}, min_eig={min_eigenvalue(n)}")
