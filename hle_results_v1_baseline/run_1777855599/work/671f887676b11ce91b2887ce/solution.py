import numpy as np

def solve():
    # The space of 3-subspaces of R^5 is the real Grassmannian Gr(3, 5).
    # Gr(3, 5) is homeomorphic to Gr(2, 5).
    # The dimension of Gr(k, n) is k(n-k). For k=2, n=5, dim = 2(3) = 6.
    # The Schubert cells are indexed by partitions lambda = (lambda_1, lambda_2)
    # where n-k >= lambda_1 >= lambda_2 >= 0.
    # Here n-k = 5-2 = 3. So 3 >= lambda_1 >= lambda_2 >= 0.
    # The cells are:
    # (0,0) dim 0
    # (1,0) dim 1
    # (1,1) dim 2
    # (2,0) dim 2
    # (2,1) dim 3
    # (2,2) dim 4
    # (3,0) dim 3
    # (3,1) dim 4
    # (3,2) dim 5
    # (3,3) dim 6 -- wait, lambda_2 <= lambda_1 <= 3.
    # Let's list them properly:
    # lambda_1 >= lambda_2 >= 0 and 3 >= lambda_1 >= lambda_2
    # (0,0) -> dim 0
    # (1,0) -> dim 1
    # (1,1) -> dim 2
    # (2,0) -> dim 2
    # (2,1) -> dim 3
    # (2,2) -> dim 4
    # (3,0) -> dim 3
    # (3,1) -> dim 4
    # (3,2) -> dim 5
    # (3,3) -> dim 6
    
    # Wait, the dimension of the cell is (n-k) + (n-k-1) + ... - (sum lambda_i)? No.
    # The dimension of the Schubert cell corresponding to lambda is sum(lambda_i).
    # Wait, the standard notation is:
    # lambda_1 >= lambda_2 >= ... >= lambda_k >= 0
    # where lambda_i <= n-k.
    # Here k=2, n=5. n-k = 3.
    # lambda_1, lambda_2 such that 3 >= lambda_1 >= lambda_2 >= 0.
    # (0,0): dim 0
    # (1,0): dim 1
    # (1,1): dim 2
    # (2,0): dim 2
    # (2,1): dim 3
    # (2,2): dim 4
    # (3,0): dim 3
    # (3,1): dim 4
    # (3,2): dim 5
    # (3,3): dim 6
    
    # However, the integral cohomology of real Grassmannians is known to have 2-torsion.
    # The rank of the torsion subgroup is the number of non-zero torsion groups.
    # But the question asks for "the rank of the torsion subgroup".
    # In the context of abelian groups, "rank" usually refers to the free rank (number of Z factors).
    # But for a torsion group, the free rank is 0.
    # If "rank" means the number of cyclic components (the size of the torsion part is the product of orders),
    # or the minimum number of generators?
    # Or maybe it's asking for the sum of the ranks of the torsion parts?
    # Let's re-read: "the rank of the torsion subgroup".
    # For a finite abelian group, the rank is often defined as the minimum number of generators.
    # But in many contexts, "rank" of a torsion group is 0.
    # Let's check if there's a specific meaning in topology.
    # Usually, "rank of H_n(X)" is the dimension of H_n(X) otimes Q.
    # If the question asks for the rank of the torsion subgroup, and the torsion subgroup is finite,
    # the rank is 0.
    # BUT, if the question is from a context where "rank" means something else...
    # Let's check the cohomology of Gr(2,5) more carefully.
    
    # The integral cohomology of real Grassmannians can be computed using the Schubert cell structure
    # and the boundary maps. The boundary maps are related to the coefficients in the Bruhat order.
    # For Gr(k, n), the torsion is all 2-torsion.
    
    # Let's search for "rank of the torsion subgroup of the integral cohomology ring".
    # This phrasing is slightly unusual if the answer is 0.
    # Maybe it means the number of elements in the torsion subgroup? No, that's "order".
    # Maybe it means the number of torsion summands?
    
    # Let's look at Gr(2, 5) again.
    # The Betti numbers (over Q) are the number of cells of each dimension.
    # But we want the torsion.
    
    # Let's try to find the cohomology of Gr(2, 5) or Gr(3, 5) online.
    # A known result: H^*(Gr(k, n); Z) has torsion only of order 2.
    # The question might be asking for the number of torsion elements or something else.
    # Wait, "rank of the torsion subgroup" is 0 for any finite group.
    # Is it possible the torsion subgroup is not finite? No, cohomology of a manifold is finitely generated.
    # If the torsion subgroup is finite, its rank is 0.
    # This would be a "trick" question.
    
    # Let's reconsider the term "rank".
    # In some contexts, "rank" of a torsion group refers to the number of generators.
    # In others, it's 0.
    # Let's check if there's any other interpretation.
    # Could it be the rank of the torsion part of the *ring*? No, that doesn't make sense.
    
    # Let's search for "rank of the torsion subgroup" in a topology context.
    # Usually, "rank of H_n(X)" is the rank of the free part.
    # "The torsion subgroup" is the set of all torsion elements.
    # The rank of a torsion group is 0.
    
    # Let's search for "rank of the torsion subgroup of the integral cohomology ring of the space of 3-subspaces of R^5".
    # Maybe the question is "What is the torsion subgroup..." or "What is the rank of the cohomology group..."
    # If the question is "What is the rank of the torsion subgroup", and the answer is 0, it's a very simple question.
    # Let's check if there's any case where the torsion subgroup has a non-zero rank.
    # No, by definition, the rank of a torsion group is 0.
    
    # Let's search for the specific problem.
    pass

solve()
