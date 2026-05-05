import itertools

def solve_efficient(k):
    # The condition is that the graph (V, A) has max degree <= 2.
    # Such graphs are collections of disjoint paths and cycles.
    # However, we need to sum (-1)^|A| over all such A.
    # This is equivalent to the coefficient of x^0 in some polynomial? No.
    # Let's use the property that the sum is over all subsets of edges.
    # This is the same as the sum of (-1)^|A| over all A in Delta_k.
    # The sum is S = sum_{A in Delta_k} (-1)^|A|.
    # The reduced Euler characteristic is hat_chi = 1 - S (if we include the empty set as dimension -1).
    # Wait, the definition of simplicial complex: faces are non-empty subsets.
    # Let F be the set of faces.
    # chi = sum_{i=0}^d (-1)^i f_i, where f_i is the number of faces of dimension i.
    # Dimension of face A is |A| - 1.
    # chi = sum_{A in F} (-1)^{|A|-1} = sum_{A in F} -(-1)^{|A|} = - sum_{A in F} (-1)^{|A|}.
    # hat_chi = chi - 1 = - sum_{A in F} (-1)^{|A|} - 1.
    # Wait, the empty set is usually included in the Euler characteristic calculation as dimension -1.
    # The reduced Euler characteristic is hat_chi = sum_{i=-1}^d (-1)^i f_i.
    # f_{-1} = 1 (the empty set).
    # hat_chi = (-1)^{-1} f_{-1} + (-1)^0 f_0 + (-1)^1 f_1 + ...
    # hat_chi = -1 + f_0 - f_1 + f_2 - ...
    # The problem says "A non-empty subset A is independent (also called a 'face')".
    # So the faces are the non-empty sets.
    # Let S = sum_{A in Delta_k} (-1)^{|A|-1}.
    # Then chi = S.
    # hat_chi = chi - 1 = S - 1.
    # Let's re-verify with k=3.
    # k=3, edges = {12, 23, 31}.
    # Subsets A with max degree <= 2:
    # |A|=0: {} (not a face)
    # |A|=1: {12}, {23}, {31} (3 faces)
    # |A|=2: {12,23}, {23,31}, {31,12} (3 faces)
    # |A|=3: {12,23,31} (1 face, max degree is 2)
    # chi = f_0 - f_1 + f_2 = 3 - 3 + 1 = 1.
    # hat_chi = chi - 1 = 0.
    # My code for k=3 gave hat_chi = 0.
    # Let's re-check k=3 with the code's logic.
    # In my code: total_sum = sum_{A in Delta_k} (-1)^|A|.
    # For k=3:
    # r=0: subset={}, degs=[0,0,0], total_sum += 1
    # r=1: 3 subsets, degs=[1,1,0] etc, total_sum -= 3
    # r=2: 3 subsets, degs=[2,1,1] etc, total_sum += 3
    # r=3: 1 subset, degs=[2,2,2], total_sum -= 1
    # total_sum = 1 - 3 + 3 - 1 = 0.
    # My code returns -total_sum = 0.
    # Wait, if total_sum = sum_{A in Delta_k} (-1)^|A|, then
    # hat_chi = -1 + f_0 - f_1 + ... = -1 + sum_{A in Delta_k} (-1)^{|A|-1}
    # hat_chi = -1 - sum_{A in Delta_k} (-1)^{|A|} = -1 - total_sum.
    # Let's re-calculate k=3: total_sum = 0, so hat_chi = -1.
    # But my code said hat_chi = 0. Let's re-check the code.
    # The code: for r in range(m+1): if all(d <= 2): if r%2==0: total_sum += 1 else: total_sum -= 1
    # For k=3, m=3:
    # r=0: total_sum = 1
    # r=1: total_sum = 1 - 3 = -2
    # r=2: total_sum = -2 + 3 = 1
    # r=3: total_sum = 1 - 1 = 0
    # So total_sum = 0.
    # If total_sum = 0, then hat_chi = -1 - 0 = -1.
    # Wait, my code for k=3 returned 0. Let's look at the code again.
    # total_sum = 0. return -total_sum = 0.
    # Let's re-calculate chi for k=3.
    # Faces:
    # dim 0: {12}, {23}, {31} (3 faces)
    # dim 1: {12,23}, {23,31}, {31,12} (3 faces)
    # dim 2: {12,23,31} (1 face)
    # chi = 3 - 3 + 1 = 1.
    # hat_chi = chi - 1 = 0.
    # So hat_chi = 0 for k=3.
    # My code: total_sum = 0. return -total_sum = 0. Correct.
    # Let's re-calculate k=5.
    # m = 10.
    # total_sum = sum_{A in Delta_5} (-1)^|A|.
    # hat_chi = -1 - total_sum.
    # Wait, if total_sum = sum_{A in Delta_k} (-1)^|A|, then
    # sum_{A in Delta_k} (-1)^{|A|-1} = -total_sum.
    # chi = -total_sum.
    # hat_chi = chi - 1 = -total_sum - 1.
    # Let's re-run the code with the correct formula.
    pass

def solve_correct(k):
    edges = list(itertools.combinations(range(k), 2))
    m = len(edges)
    total_sum = 0
    for r in range(m + 1):
        for subset in itertools.combinations(edges, r):
            degrees = [0] * k
            for u, v in subset:
                degrees[u] += 1
                degrees[v] += 1
            if all(d <= 2 for d in degrees):
                if r % 2 == 0:
                    total_sum += 1
                else:
                    total_sum -= 1
    # total_sum = sum_{A in Delta_k} (-1)^|A|
    # chi = sum_{A in Delta_k} (-1)^{|A|-1} = -total_sum
    # hat_chi = chi - 1 = -total_sum - 1
    # Wait, if r=0 is included in total_sum, then total_sum = 1 - f_0 + f_1 - f_2 ...
    # total_sum = 1 - (f_0 - f_1 + f_2 ...) = 1 - chi.
    # So chi = 1 - total_sum.
    # hat_chi = chi - 1 = -total_sum.
    # Let's check k=3 again. total_sum = 0. hat_chi = 0. Correct.
    # Let's check k=5.
    # For k=5, m=10.
    # Let's run the code.
    return -total_sum

for k in [3, 5, 7]:
    print(f"k={k}, hat_chi={solve_correct(k)}, hat_chi % k={solve_correct(k) % k}")
