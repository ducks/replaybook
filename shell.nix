{ pkgs ? import <nixpkgs> {} }:

let
  rust-overlay = import (builtins.fetchTarball
    "https://github.com/oxalica/rust-overlay/archive/master.tar.gz");
  pkgs' = import <nixpkgs> { overlays = [ rust-overlay ]; };
  rust = pkgs'.rust-bin.stable."1.88.0".default;
  python = pkgs'.python3.withPackages (packages: with packages; [ jinja2 ]);
in
pkgs'.mkShell {
  buildInputs = with pkgs'; [
    rust
    rust-analyzer
    rustfmt
    clippy
    docker
    docker-compose
    python
  ];

  RUST_BACKTRACE = 1;
}
