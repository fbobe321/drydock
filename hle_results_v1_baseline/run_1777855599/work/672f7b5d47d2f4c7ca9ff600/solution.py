import chess

fen = "8/3p4/1kpP4/p1q5/P7/8/5Q2/6K1 w - - 0 1"
board = chess.Board(fen)

print(f"FEN: {fen}")
print(f"White to move.")

print("nChecking for checks:")
for move in board.legal_moves:
    board.push(move)
    if board.is_check():
        print(f"Move {move.uci()} is a check.")
    board.pop()

print("nAnalyzing moves (Material only):")
for move in board.legal_moves:
    board.push(move)
    # board.material is not a property, we use board.piece_material() or similar
    # But we can just sum the values manually.
    score = sum(board.piece_map().values()) # This is not correct for material value
    # Let's use a better way to get material score
    score = 0
    for piece_type in chess.PIECE_TYPES:
        score += len(board.pieces(piece_type, chess.WHITE)) * chess.Piece(piece_type, chess.WHITE).piece_type # This is also wrong
    
    # Let's just use the standard way to get material
    # material = board.piece_map() ... no.
    # Let's just use the simple piece values.
    
    # Correct way to get material score:
    white_material = 0
    black_material = 0
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
    
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            if piece.color == chess.WHITE:
                white_material += values[piece.piece_type]
            else:
                black_material += values[piece.piece_type]
    
    score = white_material - black_material
    print(f"Move: {move.uci()}, Material Score (W-B): {score}")
    board.pop()

