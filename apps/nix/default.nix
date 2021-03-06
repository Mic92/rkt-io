with import (builtins.fetchTarball {
  url = "https://github.com/NixOS/nixpkgs/archive/5e6825612c9114c12eb9a99c0b42a5aba6289908.tar.gz";
  sha256 = "1mxs4z9jgcvi7gliqxc77k16fv22ry42rqgss5mnqyls4p2zk3c7";
}) {};

let

  busybox = pkgsMusl.busybox.overrideAttrs (old: {
    CFLAGS = "-pie";
    patches = (old.patches or []) ++ [ ./busybox-mlock.patch ];
  });

  # Runs out-of-memory wit
  samba = (pkgsMusl.samba.override {
    enableRegedit = true;
  }).overrideAttrs (old: {
    patches = old.patches ++ [
      ./musl_uintptr.patch
      ./netdb-defines.patch
    ];
    buildInputs = with pkgsMusl; [
      readline popt iniparser libtirpc
      libbsd libarchive zlib libiconv libunwind
      ncurses
    ];

    nativeBuildInputs = with pkgsMusl; [
      python2 pkgconfig perl gettext
      libxslt docbook_xsl docbook_xml_dtd_42
    ];

    configureFlags = [
      "--with-shared-modules=ALL"
      "--enable-fhs"
      "--sysconfdir=/etc"
      "--localstatedir=/var"
      "--without-ad-dc"
      "--without-ads"
      "--without-systemd"
      "--without-ldap"
      "--without-pam"
    ];
    debugSymbols = false;
  });

  redis = pkgsMusl.redis.overrideAttrs (old: {
    name = "redis-6.0.6";
    buildInputs = [ pkgsMusl.openssl ]; # no lua/systemd
    nativeBuildInputs = [ pkgsMusl.pkg-config ];
    makeFlags = [ "MALLOC=libc" "PREFIX=$(out)" "BUILD_TLS=yes" ];
    src = pkgsMusl.fetchurl {
      url = "http://download.redis.io/releases/redis-6.0.6.tar.gz";
      sha256 = "151x6qicmrmlxkmiwi2vdq8p50d52b9gglp8csag6pmgcfqlkb8j";
    };
  });

  redis-scone = (redis.override ({
    stdenv = sconeStdenv;
  })).overrideAttrs (old: {
    buildInputs = [
      (openssl.override {
        stdenv = sconeStdenv;
        static = true;
      })
    ];
  });

  mysql = pkgsMusl.callPackage ./mysql-5.5.x.nix {};

  mysqlScone =  mysql.override({
    stdenv = sconeStdenv;
    enableStatic = true;
  });

  mysql-image = { sgx-lkl-run ? "sgx-lkl-run" }: runImage {
    pkg = mysql;
    inherit sgx-lkl-run;
    command = [ "bin/mysqld" "--socket=/tmp/mysql.sock" ];
    extraFiles = {
      "/etc/my.cnf" = ''
        [mysqld]
        user=root
        datadir=${mysqlDatadir}
      '';
      "/etc/resolv.conf" = "";
      "/etc/services" = "${iana-etc}/etc/services";
      "/var/lib/mysql/.keep" = "";
      "/run/mysqld/.keep" = "";
    };
    extraCommands = ''
      export PATH=$PATH:${lib.getBin nettools}/bin
      ${mysql}/bin/mysql_install_db --datadir=$(readlink -f root/${mysqlDatadir}) --basedir=${mysql}
      ${mysql}/bin/mysqld_safe --datadir=$(readlink -f root/${mysqlDatadir}) --socket=$TMPDIR/mysql.sock &
      while [[ ! -e $TMPDIR/mysql.sock ]]; do
        sleep 1
      done
      ${mysql}/bin/mysql -u root --socket=$TMPDIR/mysql.sock <<EOF
      GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' IDENTIFIED BY 'root' WITH GRANT OPTION;
      CREATE DATABASE root;
      FLUSH PRIVILEGES;
      EOF
   '';
  };

  mysql-image-scone = mysql-image.override {
    pkg = mysqlScone;
  };

  fio = pkgsMusl.fio.overrideAttrs (old: {
    src = fetchFromGitHub {
      owner = "Mic92";
      repo = "fio";
      rev = "c0dfe40ba805153685e23bfd257d6db7e722088b";
      sha256 = "0ikjkd1b258lvwv4ay3chksd7qbnnlmp80p15jcaw1czzjg4fzsr";
    };
    patches = (old.patches or []) ++ [
      ./fio-pool-size.patch
    ];
    postConfigure = ''
      sed -i '/#define CONFIG_TLS_THREAD/d' config-host.h
    '';
    configureFlags = [ "--disable-shm" ];
  });

  fio-graphene = pkgs.fio.overrideAttrs (old: {
    src = ./fio-src;
    patches = (old.patches or []) ++ [
      ./fio-pool-size.patch
    ];
    buildInputs = [];
    postConfigure = ''
      sed -i '/#define CONFIG_TLS_THREAD/d' config-host.h
    '';
    configureFlags = [ "--disable-shm" ];
  });

  mysqlDatadir = "/var/lib/mysql";

  #fio-scone = pkgsMusl.fio;
  fio-scone = fio.override {
    stdenv = sconeStdenv;
  };
  #iperf3-scone = pkgs.callPackage ./iperf {
  #  stdenv = sconeStdenv;
  #  enableStatic = true;
  #};

  python-scripts = pkgsMusl.callPackage ./python-scripts {};

  iperf2 = pkgsMusl.iperf2.overrideAttrs (old: {
    src = ./iperf-2.0.13;
    configureFlags = (old.configureFlags or []) ++ [ "--enable-ipv6" "--disable-threads" ];
  });

  fioCommand = [
    "bin/fio"
    #"--output-format=json+"
    "fio-rand-RW.job"
  ];
  iozone = pkgsMusl.iozone.overrideAttrs (attr: {
    # disable gnuplot
    preFixup = "";
    patches = [ ./iozone-max-buffer-size.patch ];
    NIX_CFLAGS_COMPILE = [ "-USHARED_MEM" "-DNO_FORK" ];
  });

  iperf3 = pkgsMusl.callPackage ./iperf {};
  iperf3-scone = iperf3.override {
    stdenv = sconeStdenv;
    enableStatic = true;
  };
  iperf3-graphene = pkgs.callPackage ./iperf {};
  hello-graphene = pkgs.callPackage ./hello {};

  inherit (pkgs.callPackages ./scone {})
    scone-cc sconeStdenv sconeEnv scone-unwrapped;
  inherit (pkgs.callPackages ./graphene {}) runGraphene;
  sgx-lkl = pkgs.callPackage ./sgx-lkl {};

  buildImage = callPackage ./build-image.nix {
    scone = scone-unwrapped;
  };
  runImage = callPackage ./run-image.nix {
    inherit buildImage;
  };
  iotest-image = pkgs.callPackage ./iotest-image.nix {
    inherit mysql mysqlDatadir buildImage;
  };

  pthread-socket = pkgsMusl.callPackage ./pthread-socket {};
  network-test = pkgsMusl.callPackage ./network-test {};
  latency-test = pkgsMusl.callPackage ./latency-test {};
  memcpy-test  = pkgsMusl.callPackage ./memcpy-test {};


  simpleio-musl = pkgsMusl.callPackage ./simpleio {};
  simpleio-scone = simpleio-musl.override {
    stdenv = sconeStdenv;
  };
  nginxConfigureFlags = dir: [
    "--with-threads"
    "--with-http_ssl_module"
    "--http-log-path=${dir}/nginx/access.log"
    "--error-log-path=${dir}/nginx/error.log"
    "--pid-path=${dir}/nginx/nginx.pid"
    "--http-client-body-temp-path=${dir}/nginx/client_body"
    "--http-proxy-temp-path=${dir}/nginx/proxy"
    "--http-fastcgi-temp-path=${dir}/nginx/fastcgi"
    "--http-uwsgi-temp-path=${dir}/nginx/uwsgi"
    "--http-scgi-temp-path=${dir}/nginx/scgi"
  ];
  nginx = (pkgsMusl.nginx.override {
    gd = null;
    geoip = null;
    libxslt = null;
    withStream = false;
  }).overrideAttrs (old: {
    configureFlags = nginxConfigureFlags "/proc/self/cwd";
  });
  nginx-scone = (nginx.override {
    stdenv = sconeStdenv;
  }).overrideAttrs (old: {
    configureFlags = nginxConfigureFlags "${toString ./.}/iotest-mnt";
    buildInputs = [
      ((pcre.override {
        stdenv = sconeStdenv;
      }).overrideAttrs (old: {
        doCheck = false;
        dontDisableStatic = true;
        configureFlags = old.configureFlags ++ [ "--disable-shared" ];
      }))
      (openssl.override {
        stdenv = sconeStdenv;
        static = true;
      })
      ((zlib.override {
        static = true;
        shared = false;
        splitStaticOutput = false;
      }).overrideAttrs (old: {
        stdenv = sconeStdenv;
      }))
    ];
  });
  sqlite-speedtest = (pkgsMusl.sqlite.overrideAttrs (old: {
    src = fetchFromGitHub {
      owner="harshanavkis";
      repo="sqlite-speedtest-custom";
      rev = "591be835b8e73bc79f1e6d7766a78e20b915d94f";
      sha256 = "08wpy6739hgbcf7jyklq66vjhy28yyyaxmfdlgzgcy1y584zmh3g";
    };
    buildInputs = [ pkgsMusl.tcl ];
    outputs = ["out"];
    makeFlags = ["speedtest1"];
    installPhase = ''
        mkdir -p $out/bin
        cp speedtest1 $out/bin
      '';
  }));

  sqlite-speedtest-scone = sqlite-speedtest.override {
    stdenv = sconeStdenv;
  };
in {
  musl = pkgs.musl;

  iozone = runImage {
    pkg = iozone;
    command = [ "bin/iozone" ];
  };

  pthread-socket = runImage {
    pkg = pthread-socket;
    command = [ "bin/pthread-socket" ];
  };

  network-test-sgx-io = runImage {
    pkg = network-test;
    command = [ "bin/network-test" ];
    #native = true;
  };

  network-test-sgx-lkl = runImage {
    pkg = network-test;
    command = [ "bin/network-test" ];
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
  };

  latency-test = runImage {
    pkg = latency-test;
    command = [ "bin/latency-test" ];
  };

  memcpy-test-sgx-io = runImage {
    pkg = memcpy-test;
    command = [ "bin/memcpy-test" "0"];
  };

  simpleio-sgx-io = runImage {
    pkg = simpleio-musl;
    command = [ "bin/simpleio" ];
  };

  simpleio-sgx-lkl = runImage {
    pkg = simpleio-musl;
    #sgx-lkl-run = toString ../../../sgx-lkl-master/build/sgx-lkl-run;
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
    command = [ "bin/simpleio" ];
  };

  simpleio-scone = runImage {
    pkg = simpleio-scone;
    native = true;
    command = [ "bin/simpleio" ];
  };

  simpleio-native = runImage {
    pkg = simpleio-musl;
    native = true;
    command = [ "bin/simpleio" ];
  };

  iperf-sgx-io = runImage {
    pkg = iperf3;
    command = [ "bin/iperf3" "4" ];
  };

  iperf-sgx-lkl = runImage {
    pkg = iperf3;
    # debugging
    #sgx-lkl-run = toString ../../../sgx-lkl-org/build/sgx-lkl-run;
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
    command = [ "bin/iperf3" "4" ];
  };

  iperf-native = runImage {
    pkg = iperf3;
    native = true;
    command = [ "bin/iperf3" "4" ];
  };

  iperf-scone = runImage {
    pkg = iperf3-scone;
    native = true;
    command = [ "bin/iperf3" "4" ];
  };

  inherit sconeStdenv sconeEnv;

  # provides scone command
  scone = scone-unwrapped;

  iperf3-graphene = runGraphene {
    pkg = iperf3-graphene;
    command = ["bin/iperf3"];
    # our iperf binds each core to a different port
    ports = lib.range 5201 5299;
  };

  fio-graphene = runGraphene {
    pkg = fio-graphene;
    command = ["bin/fio"];
  };


  hello-graphene = runGraphene {
    pkg = hello-graphene;
    command = ["bin/hello"];
  };

  curl-remote = pkgsMusl.curl;

  parallel-iperf = stdenv.mkDerivation {
    name = "parallel-iperf";
    src = ./parallel-iperf.py;
    dontUnpack = true;
    buildInputs = [ python3 ];
    nativeBuildInputs = [ makeWrapper python3.pkgs.wrapPython ];
    makeWrapperArgs = [
      "--prefix" "PATH" ":" "${lib.makeBinPath [ pkgsMusl.iperf] }"
    ];
    installPhase = ''
      install -D -m755 $src $out/bin/parallel-iperf
      ln -s ${pkgsMusl.iperf3}/bin/iperf $out/bin/iperf
      patchPythonScript $out/bin/parallel-iperf
    '';
  };

  iperf-client = iperf3;

  iproute = runImage {
    pkg = pkgsMusl.iproute;
    command = [ "bin/ip" "a" ];
  };

  ping = runImage {
    pkg = busybox;
    command = [ "bin/ping" "10.0.42.1" ];
  };

  dd-sgx-io = runImage {
    pkg = busybox;
    command = [ "bin/dd" "if=/dev/spdk0" "of=/dev/null" ];
  };

  ls = runImage {
    pkg = busybox;
    command = [ "bin/ls" "/dev/" ];
  };

  touch = runImage {
    pkg = busybox;
    command = [ "bin/touch" "/mnt/vdb/foobar" ];
  };

  arping = runImage {
    pkg = busybox;
    command = [ "bin/arping" "-I" "eth0" "10.0.2.2" ];
  };

  fio-sgx-io = runImage {
    pkg = fio;
    command = fioCommand;
  };

  fio-sgx-lkl = runImage {
    pkg = fio;
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
    #sgx-lkl-run = toString ../../../sgx-lkl-org/build/sgx-lkl-run;
    command = [ "bin/fio" ];
  };

  fio-native = runImage {
    pkg = fio;
    native = true;
    command = fioCommand;
  };

  fio-scone = runImage {
    pkg = fio-scone;
    native = true;
    command = fioCommand;
  };

  ioping = runImage {
    pkg = pkgsMusl.ioping;
    command = [ "bin/ioping" ];
  };

  hdparm-sgx-io = runImage {
    pkg = busybox;
    command = [ "bin/hdparm" "-Tt" "/dev/spdk0" ];
  };

  hdparm-sgx-lkl = runImage {
    pkg = busybox;
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
    command = [ "bin/hdparm" "-Tt" "/dev/spdk0" ];
  };

  hdparm-native = runImage {
    pkg = busybox;
    native = true;
    command = [ "bin/hdparm" "-Tt" "/dev/spdk0" ];
  };

  ycsb-native = pkgs.callPackage ./ycsb {};

  redis-cli = redis;

  redis-native = runImage {
    pkg = redis;
    native = true;
    command = [ "bin/redis-server" "--protected-mode" "no" ];
  };
  
  redis-sgx-lkl = runImage {
    pkg = redis;
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
    command = [ "bin/redis-server" "--protected-mode" "no" ];
  };

  redis-sgx-io = runImage {
    pkg = redis;
    command = [ "bin/redis-server" "--protected-mode" "no" ];
  };

  redis-scone = runImage {
    pkg = redis-scone;
    command = [ "bin/redis-server" "--protected-mode" "no" ];
    native = true;
  };

  mysql-sgx-io = mysql-image {};
  mysql-sgx-lkl = mysql-image {
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
  };
  mysql-native = runImage {
    pkg = mysql;
    native = true;
    command = [ "bin/mysqld" ];
  };

  mysql-scone = runImage {
    pkg = mysqlScone;
    native = true;
    command = [ "bin/mysqld" ];
  };

  perl = runImage {
    pkg = pkgsMusl.perl;
    command = [ "bin/perl" "-e" "print 'foo\n';" ];
  };

  samba = runImage {
    pkg = samba;
    command = [ "bin/smbd" "--interactive" "--configfile=/etc/smb.conf" ];
    extraFiles = {
      "/etc/smb.conf" = ''
        registry shares = no

        [Anonymous]
        path = /
        browsable = yes
        writable = yes
        read only = no
        force user = nobody
      '';
      "/var/log/samba/.keep" = "";
      "/var/lock/samba/.keep" = "";
      "/var/lib/samba/private/.keep" = "";
      "/var/run/samba/.keep" = "";
    };
  };

  sysbench = pkgs.sysbench;

  netcat = runImage {
    pkg = pkgsMusl.busybox;
    command = [ "bin/nc" "10.0.42.1" ];
  };

  netcat-native = pkgsMusl.netcat;

  python-scripts = runImage {
    pkg = pkgsMusl.python3Minimal;
    extraFiles = {
      "/introspect-blocks.py" = builtins.readFile ./python-scripts/introspect-blocks.py;
    };
    command = [ "bin/python3" "introspect-blocks.py" ];
  };

  iotest-image-scone = iotest-image.override {
    sconeEncryptedDir = "${toString ./.}/iotest-mnt";
  };
  inherit iotest-image;

  nginx-native = runImage {
    pkg = nginx;
    command = [ "bin/nginx" "-c" "/etc/nginx.conf" ];
    native = true;
  };

  nginx-sgx-io = runImage {
    pkg = nginx;
    command = [ "bin/nginx" "-c" "/etc/nginx.conf" ];
  };

  nginx-sgx-lkl = runImage {
    pkg = nginx;
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
    command = [ "bin/nginx" "-c" "/etc/nginx.conf" ];
  };

  nginx-scone = runImage {
    pkg = nginx-scone;
    command = [ "bin/nginx" "-c" "/etc/nginx.conf" ];
    native = true;
  };

  sqlite-native = runImage {
    pkg = sqlite-speedtest;
    native = true;
    command = [ "bin/speedtest1" "--size" "10" "--journal" "delete" "bench.db" ];
  };

  sqlite-sgx-io = runImage {
    pkg = sqlite-speedtest;
    command = [ "bin/speedtest1" "--size" "10" "--journal" "delete" "bench.db" ];
  };

  sqlite-sgx-lkl = runImage {
    pkg = sqlite-speedtest;
    sgx-lkl-run = "${sgx-lkl}/bin/sgx-lkl-run";
    command = [ "bin/speedtest1" "--size" "10" "--journal" "delete" "bench.db" ];
  };

  sqlite-scone = runImage {
    pkg = sqlite-speedtest-scone;
    native = true;
    command = [ "bin/speedtest1" "--size" "10" "--journal" "delete" "bench.db" ];
  };

  wrk-bench = pkgsMusl.wrk;
}
