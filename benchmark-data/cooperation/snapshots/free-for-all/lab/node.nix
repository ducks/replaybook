{ lib, pkgs, ... }:
let
  node = builtins.getEnv "REPLAYBOOK_PEER_NODE";
  index = if node == "alpha" then 1 else 2;
  sshPort = builtins.getEnv "REPLAYBOOK_PEER_SSH_PORT";
  sharedDisk = builtins.getEnv "REPLAYBOOK_PEER_SHARED_DISK";
  wirePort = builtins.getEnv "REPLAYBOOK_PEER_WIRE_PORT";
  publicKey = builtins.getEnv "REPLAYBOOK_HOST_PUBLIC_KEY_FILE";
  claux = builtins.getEnv "REPLAYBOOK_HOST_CLAUX_BINARY";
  clauxPackage = pkgs.runCommand "peer-lab-claux" {} ''
    install -Dm755 ${builtins.path { path = claux; name = "claux-binary"; }} $out/bin/claux
  '';
in {
  assertions = [
    { assertion = builtins.elem node [ "alpha" "beta" ]; message = "select alpha or beta"; }
    { assertion = sharedDisk != ""; message = "shared disk required"; }
    { assertion = publicKey != "" && builtins.pathExists publicKey; message = "controller public key required"; }
  ];
  networking.hostName = "peer-${node}";
  networking.useDHCP = false;
  networking.interfaces.eth0.useDHCP = true;
  networking.interfaces.eth1.ipv4.addresses = [{ address = "10.77.0.${toString index}"; prefixLength = 30; }];
  networking.firewall.trustedInterfaces = [ "eth1" ];
  networking.firewall.allowedTCPPorts = [ 22 ];
  environment.loginShellInit = lib.mkForce "";
  environment.systemPackages = with pkgs; [ bash cacert curl git jq python3 iproute2 iputils netcat-openbsd ripgrep clauxPackage ];
  programs.nix-ld.enable = true;
  services.openssh = {
    enable = true;
    settings = { PasswordAuthentication = false; KbdInteractiveAuthentication = false; PermitRootLogin = "prohibit-password"; };
  };
  users.users.root.openssh.authorizedKeys.keys = [ (lib.removeSuffix "\n" (builtins.readFile publicKey)) ];
  virtualisation = {
    cores = 1;
    memorySize = 1536;
    diskSize = 8192;
    vlans = lib.mkForce [];
    restrictNetwork = true;
    useNixStoreImage = true;
    mountHostNixStore = false;
    sharedDirectories = lib.mkForce {};
    writableStore = true;
    qemu.networkingOptions = lib.mkForce [
      "-netdev user,id=mgmt,restrict=on,hostfwd=tcp:127.0.0.1:${sshPort}-:22"
      "-device virtio-net-pci,netdev=mgmt,mac=52:54:00:66:00:0${toString index}"
      "-netdev socket,id=peer,connect=127.0.0.1:${wirePort}"
      "-device virtio-net-pci,netdev=peer,mac=52:54:00:77:00:0${toString index}"
      "-drive file=${sharedDisk},if=none,format=raw,id=shared,cache=none,file.locking=off"
      "-device virtio-blk-pci,drive=shared,serial=peer-shared,share-rw=on"
    ];
  };
  system.stateVersion = "24.05";
}
