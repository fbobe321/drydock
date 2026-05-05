import numpy as np

def f(t):
    return np.cos(np.pi * t)**2

def g(x):
    return f(np.sin(np.pi * x))

def h(y):
    return f(np.cos(2 * np.pi * y))

def solve():
    def K(x):
        return h(g(x)) - x
    
    # Use a very fine grid to find all roots
    N = 5000
    x_grid = np.linspace(0, 1, N)
    K_grid = np.array([K(x) for x in x_grid])
    
    roots = []
    
    # 1. Check for sign changes
    for i in range(N - 1):
        x1, x2 = x_grid[i], x_grid[i+1]
        v1, v2 = K_grid[i], K_grid[i+1]
        if v1 * v2 <= 0:
            low, high = x1, x2
            for _ in range(60):
                mid = (low + high) / 2
                if K(mid) * v1 <= 0:
                    high = mid
                else:
                    low = mid
            root = (low + high) / 2
            if not roots or abs(root - roots[-1]) > 1e-7:
                roots.append(root)
        elif abs(v1) < 1e-9:
            if not roots or abs(x1 - roots[-1]) > 1e-7:
                roots.append(x1)

    # 2. Check for local minima/maxima that touch zero
    # We'll use a finer grid to find where K(x) might be zero without a sign change
    N_fine = 10000
    x_fine = np.linspace(0, 1, N_fine)
    K_fine = np.array([K(x) for x in x_fine])
    
    for i in range(1, N_fine - 1):
        if abs(K_fine[i]) < 1e-7:
            # Check if it's a local extremum (not a sign change)
            if K_fine[i-1] * K_fine[i+1] > 0:
                # It's a touch point. Refine it.
                low, high = x_fine[i-1], x_fine[i+1]
                for _ in range(60):
                    m1 = low + (high - low) / 3
                    m2 = high - (high - low) / 3
                    if abs(K(m1)) < abs(K(m2)):
                        high = m2
                    else:
                        low = m1
                root = (low + high) / 2
                if abs(K(root)) < 1e-7:
                    if not roots or abs(root - roots[-1]) > 1e-7:
                        roots.append(root)

    # Check endpoints
    if abs(K(0)) < 1e-7:
        if not roots or abs(0 - roots[-1]) > 1e-7:
            roots.append(0.0)
    if abs(K(1)) < 1e-7:
        if not roots or abs(1 - roots[-1]) > 1e-7:
            roots.append(1.0)

    roots.sort()
    return roots

roots = solve()
print(f"Number of roots: {len(roots)}")

def g_correct(x):
    return np.cos(np.pi * np.sin(np.pi * x))**2

def h_correct(y):
    return np.cos(np.pi * np.cos(2 * np.pi * y))**2

integers_count = 0
for x in roots:
    y = g_correct(x)
    # Check if x is an integer (0 or 1) or y is an integer (0 or 1)
    if abs(x - 0) < 1e-7 or abs(x - 1) < 1e-7 or abs(y - 0) < 1e-7 or abs(y - 1) < 1e-7:
        integers_count += 1

print(f"Pairs with at least one integer: {integers_count}")
