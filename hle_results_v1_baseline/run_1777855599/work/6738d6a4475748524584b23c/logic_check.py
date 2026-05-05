import math

def check_options():
    # O1: b=3, d=4
    b = 3
    d = 4
    
    # A: (1 - 1/e)^(d-1)
    A = (1 - 1/math.e)**(d-1)
    
    # B: phi / (1 + phi) where phi = (1 + sqrt(5))/2
    phi = (1 + 5**0.5) / 2
    B = phi / (1 + phi)
    
    # C: b^(d-2)
    C_val = b**(d-2)
    
    # D: ln(b)/d
    D = math.log(b) / d
    
    # E: 1/b^d
    E = 1 / (b**d)
    
    print(f"A: {A}")
    print(f"B: {B}")
    print(f"C (threshold): {C_val}")
    print(f"D: {D}")
    print(f"E: {E}")

check_options()
