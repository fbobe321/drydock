import itertools

def is_associative(table):
    for i in range(3):
        for j in range(3):
            for k in range(3):
                if table[i][table[j][k]] != table[table[i][j]][k]:
                    return False
    return True

def get_isomorphism_class(table):
    # table is a 3x3 list of lists
    # elements are 0, 1, 2. 0 is identity.
    # Permutations of {1, 2}
    perms = [(1, 2), (2, 1)]
    
    # We want to find the "canonical" representative of the isomorphism class.
    # We can just find all isomorphic tables and pick the lexicographically smallest one.
    
    isomorphic_tables = []
    
    # The identity must be mapped to the identity.
    # So the permutation must fix 0.
    for p in [(0, 1, 2), (0, 2, 1)]:
        # p is a permutation of {0, 1, 2} such that p[0] = 0
        # Check if p is a valid isomorphism
        # A permutation p is an isomorphism if p(table[i][j]) == table[p[i]][p[j]]
        # But we want to find all tables isomorphic to the current one.
        # Actually, it's easier to just check if two tables are isomorphic.
        pass

    return None

def are_isomorphic(t1, t2):
    # t1, t2 are 3x3 lists of lists. 0 is identity.
    # A permutation f must satisfy f(0)=0 and f(t1[i][j]) = t2[f[i]][f[j]]
    # The possible permutations of {0, 1, 2} that fix 0 are:
    # (0, 1, 2) and (0, 2, 1)
    for p in [(0, 1, 2), (0, 2, 1)]:
        # Check if p is an isomorphism from t1 to t2
        # p(t1[i][j]) == t2[p[i]][p[j]]
        # Wait, the permutation is a mapping f: {0,1,2} -> {0,1,2}
        # f(t1[i][j]) == t2[f[i]][f[j]]
        # Let's define f(x) = p[x]
        match = True
        for i in range(3):
            for j in range(3):
                if p[t1[i][j]] != t2[p[i]][p[j]]:
                    match = False
                    break
            if not match: break
        if match:
            return True
    return False

# Wait, the permutation p is a mapping. 
# If p = (0, 2, 1), then f(0)=0, f(1)=2, f(2)=1.
# The condition is f(t1[i][j]) == t2[f[i]][f[j]]

def solve():
    # table[i][j]
    # table[0][i] = i, table[i][0] = i
    # Remaining: table[1][1], table[1][2], table[2][1], table[2][2]
    
    monoids = []
    
    elements = [0, 1, 2]
    # Possible values for the 4 cells
    for vals in itertools.product(elements, repeat=4):
        table = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
        # Fill identity
        for i in range(3):
            table[0][i] = i
            table[i][0] = i
        
        # Fill the rest
        table[1][1] = vals[0]
        table[1][2] = vals[1]
        table[2][1] = vals[2]
        table[2][2] = vals[3]
        
        # Check identity property (already satisfied by construction)
        # Check associativity
        if is_associative(table):
            monoids.append(table)
            
    # Count isomorphism classes
    count = 0
    used = [False] * len(monoids)
    for i in range(len(monoids)):
        if not used[i]:
            count += 1
            for j in range(i + 1, len(monoids)):
                if are_isomorphic(monoids[i], monoids[j]):
                    used[j] = True
    return count

print(solve())
