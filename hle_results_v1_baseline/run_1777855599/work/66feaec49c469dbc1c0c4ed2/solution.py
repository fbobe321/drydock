import math

def get_u_and_variance(g1, g2):
    n1 = len(g1)
    n2 = len(g2)
    N = n1 + n2
    
    combined = []
    for val in g1:
        combined.append((val, 1))
    for val in g2:
        combined.append((val, 2))
        
    combined.sort(key=lambda x: x[0])
    
    ranks = [0] * N
    tie_correction = 0
    i = 0
    while i < N:
        j = i
        while j < N - 1 and combined[j+1][0] == combined[i][0]:
            j += 1
        num_ties = j - i + 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        if num_ties > 1:
            tie_correction += (num_ties**3 - num_ties) / 12.0
        i = j + 1

    r1 = 0
    for idx in range(N):
        if combined[idx][1] == 1:
            r1 += ranks[idx]
            
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u_stat = min(u1, u2)
    mu_u = n1 * n2 / 2.0
    var_u = (n1 * n2 / (N * (N - 1))) * ((N**3 - N) / 12.0 - tie_correction)
    return u_stat, mu_u, math.sqrt(var_u)

def solve():
    target_alpha = 0.05 / 5
    for k in range(1, 100):
        n = 5 * k
        g1 = [1, 2, 3, 4, 5] * k
        g2 = [3, 3, 3, 2, 2] * k
        u, mu, sigma = get_u_and_variance(g1, g2)
        z = abs(u - mu) / sigma
        p = math.erfc(z / math.sqrt(2))
        if p < target_alpha:
            print(f"k={k}, n={n}, p={p}")
            return n
    return None

if __name__ == "__main__":
    solve()
