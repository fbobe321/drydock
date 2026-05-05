import itertools

def get_sd_group(n):
    order = 2**n
    r_order = 2**(n-1)
    # Elements: (0, i) for r^i, (1, i) for sr^i
    elements = []
    for i in range(r_order):
        elements.append((0, i))
    for i in range(r_order):
        elements.append((1, i))
    
    # Multiplication table
    # (0, i) * (0, j) = (0, (i+j) % r_order)
    # (0, i) * (1, j) = (1, (i + j*(2**(n-2)-1)) % r_order) -- Wait, let's re-check
    # s r^i = r^{i(2^{n-2}-1)} s
    # (0, i) * (1, j) = r^i * s * r^j = r^i * (r^{j(2^{n-2}-1)} s) = r^{i + j(2^{n-2}-1)} s = (1, i + j(2^{n-2}-1))
    # (1, i) * (0, j) = s * r^i * r^j = s * r^{i+j} = r^{(i+j)(2^{n-2}-1)} s = (1, (i+j)(2^{n-2}-1))
    # (1, i) * (1, j) = s * r^i * s * r^j = s * (r^{i(2^{n-2}-1)} s) * r^j = r^{i(2^{n-2}-1)} s^2 r^j = r^{i(2^{n-2}-1) + j} = (0, i(2^{n-2}-1) + j)
    
    # Let's use a more robust way:
    # s r^i s^{-1} = r^{i(2^{n-2}-1)}
    # So s r^i = r^{i(2^{n-2}-1)} s
    # (0, i) * (0, j) = (0, (i+j) % r_order)
    # (0, i) * (1, j) = r^i * s * r^j = r^i * (r^{j(2^{n-2}-1)} s) = (1, (i + j*(2**(n-2)-1)) % r_order)
    # (1, i) * (0, j) = s * r^i * r^j = s * r^{i+j} = r^{(i+j)*(2**(n-2)-1)} s = (1, ((i+j)*(2**(n-2)-1)) % r_order)
    # (1, i) * (1, j) = s * r^i * s * r^j = (s * r^i * s) * r^j = r^{i(2**(n-2)-1)} * r^j = (0, (i*(2**(n-2)-1) + j) % r_order)
    
    # Let's re-verify (1, i) * (0, j)
    # (1, i) is s * r^i.
    # (0, j) is r^j.
    # (1, i) * (0, j) = s * r^i * r^j = s * r^{i+j} = r^{(i+j)(2^{n-2}-1)} s = (1, (i+j)*(2**(n-2)-1))
    # Correct.
    
    # Let's re-verify (1, i) * (1, j)
    # (1, i) is s * r^i.
    # (1, j) is s * r^j.
    # (1, i) * (1, j) = s * r^i * s * r^j = (s * r^i * s) * r^j
    # s * r^i * s = r^{i(2^{n-2}-1)}
    # So (1, i) * (1, j) = r^{i(2^{n-2}-1)} * r^j = r^{i(2^{n-2}-1) + j} = (0, i(2^{n-2}-1) + j)
    # Correct.
    
    # Let's re-verify (0, i) * (1, j)
    # (0, i) is r^i.
    # (1, j) is s * r^j.
    # (0, i) * (1, j) = r^i * s * r^j = (r^i * s) * r^j = (s * r^{i(2^{n-2}-1)}) * r^j = s * r^{i(2^{n-2}-1) + j} = (1, (i(2**(n-2)-1) + j) % r_order)
    # Wait, my previous formula was (1, (i + j*(2**(n-2)-1)) % r_order).
    # Let's check: r^i * s * r^j = r^i * (s * r^j) = r^i * (r^{j(2^{n-2}-1)} s) = r^{i + j(2^{n-2}-1)} s.
    # Yes, (1, (i + j*(2**(n-2)-1)) % r_order).
    
    # Let's use the second one.
    
    return elements

def multiply(a, b, n):
    r_order = 2**(n-1)
    type_a, i_a = a
    type_b, i_b = b
    
    if type_a == 0:
        if type_b == 0:
            return (0, (i_a + i_b) % r_order)
        else:
            # r^i * s * r^j = r^i * r^{j(2^{n-2}-1)} s = r^{i + j(2^{n-2}-1)} s
            return (1, (i_a + i_b * (2**(n-2)-1)) % r_order)
    else:
        if type_b == 0:
            # s * r^i * r^j = s * r^{i+j} = r^{(i+j)(2^{n-2}-1)} s
            return (1, ((i_a + i_b) * (2**(n-2)-1)) % r_order)
        else:
            # s * r^i * s * r^j = r^{i(2^{n-2}-1)} * r^j = r^{i(2^{n-2}-1) + j}
            return (0, (i_a * (2**(n-2)-1) + i_b) % r_order)

def count_subgroups(n):
    # This is too slow for large n.
    # Let's use a different approach.
    pass

