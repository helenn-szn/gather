"""Run the policy gate (on the gather-gate Sprite).

    sprite-env services create gate --cmd "$(which python3)" \\
        --args "-m,scripts.run_gate" --dir "$HOME/gather" --http-port 8080
"""
from gather import gate_server

if __name__ == "__main__":
    gate_server.serve()
