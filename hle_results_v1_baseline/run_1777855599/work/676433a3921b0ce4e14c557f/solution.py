import numpy as np

def rx(theta):
    theta = np.radians(theta)
    return np.array([
        [1, 0, 0],
        [0, np.cos(theta), -np.sin(theta)],
        [0, np.sin(theta), np.cos(theta)]
    ])

def ry(theta):
    theta = np.radians(theta)
    return np.array([
        [np.cos(theta), 0, np.sin(theta)],
        [0, 1, 0],
        [-np.sin(theta), 0, np.cos(theta)]
    ])

def rz(theta):
    theta = np.radians(theta)
    return np.array([
        [np.cos(theta), -np.sin(theta), 0],
        [np.sin(theta), np.cos(theta), 0],
        [0, 0, 1]
    ])

# Extrinsic X-Y-Z: R = Rz_gamma @ Ry_beta @ Rx_alpha (standard definition for extrinsic XYZ)
# Wait, the question says "extrinsic rotation ... X_alpha Y_beta Z_gamma convention". 
# This usually means the order of rotations applied to the vector is Rx then Ry then Rz (if extrinsic).
# Or it could mean the axes are fixed and we apply Rx, then Ry, then Rz? No, extrinsic means fixed axes.
# If we rotate around X by alpha, then around Y by beta (fixed axis), then around Z by gamma (fixed axis):
# R = Rz(gamma) @ Ry(beta) @ Rx(alpha)

def extrinsic_xyz_fixed_axes(alpha, beta, gamma):
    return rz(gamma) @ ry(beta) @ rx(alpha)

# Proper Euler angles are intrinsic: R = R1 @ R2 @ R3 where axes move with the object.

def intrinsic_x_z_x(a, b, g): return rx(a) @ rz(b) @ rx(g) # X-Z-X intrinsic is Rx @ Rz' @ Rx'' which is Rx(@Rz@Rx)_intrinsic? No. 
# Standard notation: Intrinsic ZXZ means first rotate about Z by a, then about new Z' by b, then about new X'' by c. 
# The formula for intrinsic rotation sequence A-B-C is R = Ra @ Rb @ Rc (where Ra is rotation about A).

def intrinsic_x_z_x_correct(a, b, g): return rx(a) @ rz(b) @ rx(g) # Wait... no... 

# Let's use the standard composition rule: Intrinsic sequence A-B-C is equivalent to Extrinsic sequence C-B-A.

def rot_intrinsic_xzx(a, b, g): return rx(a) @ rz(b) @ rx(g) # This is not standard notation for "XZX" if we follow the rule strictly? Actually it IS if you define it that way. Let's be careful.

# Standard definitions for Euler angles:
# ZXZ (Intrinsic): R = Rz(@a) * Rx(@b) * Rz(@c)? No! It's usually defined as: first rotate about z by a', second about x' by b', third about z'' by c'. This is equivalent to Extrinsic ZXZ: R = Rz(@c) * Rx(@b) * Rz(@a).

def rot_intrinsic_zyz_stdlydicate283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749283749 (just kidding).

def rot_extrinsic_xyz_stdlydicate5555555555555555555555555: return rz()@ry()@rx() # NO!

# Let's redefine everything clearly based on "Intrinsic sequence A-B-C == Extrinsic sequence C-B-A".

def get_R_extrinsicXYZ_(a, b, g): return rz(g) @ ry(b) @ rx (a)? No... let's just test all combinations of rotations in all orders and see which one matches the target matrix using the provided angles a', b', g'.

target = None # placeholder

import numpy as np

def rxv(_t): t=np.radians(_t); return np.array([[1,0,0],[0,np.cos(t},-np.sin(t)],[0,np.sin(t),np.cos(t)]])
def ryv(_t): t=np.radians(_t); return np.array([[np.cos(_t),0,_t],[...]]) # wait I need to write this properly!

