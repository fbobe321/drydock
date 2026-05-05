import pulp

def solve(N, include_self=False):
    black_squares = []
    for x in range(N):
        for y in range(N):
            for z in range(N):
                if (x + y + z) % 2 == 1:
                    black_squares.append((x, y, z))
    
    all_cells = []
    for x in range(N):
        for y in range(N):
            for z in range(N):
                all_cells.append((x, y, z))
    
    prob = pulp.LpProblem("Unicorn_Cover", pulp.LpMinimize)
    x_vars = pulp.LpVariable.dicts("cell", range(len(all_cells)), cat=pulp.LpBinary)
    prob += pulp.lpSum([x_vars[i] for i in range(len(all_cells))])
    
    for b_idx, b_sq in enumerate(black_squares):
        attacks = []
        for i_idx, i_sq in enumerate(all_cells):
            dx = abs(i_sq[0] - b_sq[0])
            dy = abs(i_sq[1] - b_sq[1])
            dz = abs(i_sq[2] - b_sq[2])
            
            is_attack = False
            if dx == dy == dz and dx > 0:
                is_attack = True
            elif include_self and i_sq == b_sq:
                is_attack = True
            
            if is_attack:
                attacks.append(x_vars[i_idx])
        
        if attacks:
            prob += pulp.lpSum(attacks) >= 1
        else:
            # If a black square cannot be attacked by any other square, 
            # then a unicorn must be placed on it (if include_self is True).
            # But if include_self is False, this is impossible.
            # However, in a 3D grid, every square has space diagonals.
            pass

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    return pulp.value(prob.objective)

print(f"N=4, include_self=False: {solve(4, False)}")
print(f"N=4, include_self=True: {solve(4, True)}")
print(f"N=8, include_self=False: {solve(8, False)}")
print(f"N=8, include_self=True: {solve(8, True)}")
