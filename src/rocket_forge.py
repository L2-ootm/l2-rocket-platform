"""
L2 Systems - Rocket Forge v2.0
Constrói foguetes do ZERO em XML/ORK com engenharia end-to-end.
Controla: motor, nose cone, body tube, fins, parachute, materiais.
"""
import uuid
import math
import random
import xml.etree.ElementTree as ET

# =========================================================================
# BANCO DE DADOS DE MOTORES (OpenRocket standard database)
# Classe F-K, cobrindo de hobby a high-power
# Formato: (manufacturer, designation, diameter_m, length_m, delay, digest)
# digest = hash MD5 da curva de empuxo exata do OpenRocket 24.12
# =========================================================================
# Diameter/length verified directly against OpenRocket 24.12's own
# bundled motor database (openrocket/core/.../datafiles/thrustcurves/
# initial_motors.db) via extract_motors.py -- several entries here had been
# hand-transcribed incorrectly (e.g. O8000 was listed as 161mm; the real
# motor is 150mm) and every .eng file in l2_engine/motors/ was regenerated
# from the same authoritative source, so this table and the Rust-side thrust
# curves now agree by construction, not by coincidence. `designation` here is
# the exact string OpenRocket itself resolves the motor by (what
# ASTCompiler writes into <designation> for ground-truth .ork validation),
# NOT an arbitrary short alias -- for the 3 Cesaroni motors that's the
# catalog-code-prefixed "-P" form already proven to resolve correctly by
# l2_hyper/generator.py and the declarative mission JSONs. It also matches
# the .eng filename stem exactly (the Rust motor loader keys curves by this
# same string, read from each file's own header -- see
# l2_engine/src/bin/ast_eval.rs), so there is exactly one designation string
# per motor everywhere, not a Rust-side alias plus an OpenRocket-side one.
MOTOR_DATABASE = [
    # Classe F (40-80 Ns) - 29mm
    ("AeroTech", "F50T",  0.029, 0.098, 4.0, "03ffc133123d165c5bbc8d5ed240ba56"),
    ("AeroTech", "F67W",  0.029, 0.083, 4.0, "466f0a469b19b74a8c25dc0d0d7204aa"),
    # Classe G (80-160 Ns) - 29mm
    ("AeroTech", "G71R",  0.029, 0.120, 4.0, "5a4bcd84827186bf9fe4fafa153822e3"),
    ("AeroTech", "G104T", 0.029, 0.124, 7.0, "3c7dff05d78fb737406483efa42057af"),
    ("AeroTech", "G80T",  0.029, 0.124, 7.0, "92d267b2398685eac9dd417141d374e8"),
    # Classe H (160-320 Ns) - 29/38mm
    ("AeroTech", "H73J",  0.038, 0.152, 10.0, "303de1e7e61026321d23fea6b6815a25"),
    ("AeroTech", "H128W", 0.029, 0.194, 10.0, "501239de7374691072270406476cb243"),
    ("AeroTech", "H180W", 0.029, 0.238, 10.0, "30ca1252c9156ee8e93e661886b131de"),
    ("AeroTech", "H238T", 0.029, 0.194, 7.0,  "144876c4b2ec547c02d5a53e27451df9"),
    # Classe I (320-640 Ns) - 38mm
    ("AeroTech", "I161W", 0.038, 0.191, 10.0, "340f946923b8f997472ceb181f4f7605"),
    ("AeroTech", "I218R", 0.038, 0.191, 9.0,  "f9aaf185390175b167963f887b48d0fd"),
    ("AeroTech", "I357T", 0.038, 0.203, 7.0,  "c18a3e543f11f1c7369459f239a3477e"),
    ("AeroTech", "I211W", 0.038, 0.240, 10.0, "6f055ac96725164ac5814d96f109b8d5"),
    ("AeroTech", "I284W", 0.038, 0.298, 10.0, "d9da87018adebbef9d8f3d8bfbc9c1e6"),
    # Classe J (640-1280 Ns) - 38mm / 54mm
    ("AeroTech", "J350W", 0.038, 0.337, 10.0, "3425c0019512b8c67b90eac455bda968"),
    ("AeroTech", "J420R", 0.038, 0.337, 7.0,  "3a47d105d8539e108a0446496e016261"),
    ("AeroTech", "J510W", 0.038, 0.584, 10.0, "d9362a3a9b31a305f13a6c2bb0a93c38"),
    ("AeroTech", "J800T", 0.054, 0.314, 7.0,  "cb788683f45b481a33176306e89d3c3e"),
    ("Cesaroni Technology", "J360",  0.038, 0.419, 7.0, "32ce216cbcf2d58d085935084a6127d7"),
    # Classe K (1280-2560 Ns) - 54mm
    ("AeroTech", "K550W", 0.054, 0.410, 14.0, "822e1d4ed7924f9dea7a62b5f5ff2823"),
    ("AeroTech", "K700W", 0.054, 0.568, 14.0, "46ef445206f2d347f5d63e51319f3661"),
    ("AeroTech", "K1050W",0.054, 0.676, 10.0, "031c98e57ce8f1894b7e369393a24baa"),
    # Digests below (K510 through O8000) added after a live-campaign user
    # report of "Multiple motors with designation 'X' found, one chosen
    # arbitrarily" on OpenRocket load -- queried directly against the real
    # OpenRocket 24.12 JVM (Application.getMotorSetDatabase()) to find every
    # real catalog entry matching each designation and pick the certified/
    # highest-total-impulse variant (same convention already used for
    # M2500T below). Confirmed via ThrustCurveMotorSetDatabase.findMotors
    # (Java source) that a digest match alone is sufficient to resolve to
    # exactly one motor via its `digestMatches` fallback path, independent
    # of whether designation/manufacturer text also matches -- so these
    # designation strings do NOT need to match OpenRocket's own catalog
    # spelling exactly (several don't -- e.g. real K510 is catalog-coded
    # "2486K510-P", real L1000/L1150 are "HP-L1000W"/"L1150R" -- verified by
    # searching the live database directly), avoiding the much larger risk
    # of renaming a designation string that's also load-bearing in
    # l2_engine/motors/*.eng filenames/headers and every mission's
    # motor_pool.allowed_designations list.
    ("Cesaroni Technology", "K510",  0.075, 0.350, 12.0, "4d4b4db5617d8405c869bdb91f012715"),
    # Classe L (2560-5120 Ns)
    ("AeroTech", "L1000", 0.054, 0.635, 14.0, "aff226cff24500fb2ddbe474875563d3"),
    ("AeroTech", "L1150", 0.075, 0.531, 14.0, "942d2e34858b55685a666e96545db9d5"),
    ("AeroTech", "L1500T", 0.098, 0.443, 14.0, "ff0e50eaf612307c954a80f393ac2570"),
    # length corrected 0.665 -> 0.681: confirmed against the real OpenRocket
    # 24.12 motor (matching digest below) via launchMass=4.751kg/
    # burnoutMass=2.235kg exactly matching this table's/L2200G.eng's own
    # propellant(2.516kg)+total(4.751kg) mass fields -- same motor curve,
    # the length field alone was stale/wrong. l2_engine/motors/L2200G.eng's
    # header updated to match.
    ("AeroTech", "L2200G", 0.075, 0.681, 14.0, "ec3118a20080f2274a2dd6d6b840895a"),
    # Classe M (5120-10240 Ns)
    ("AeroTech", "M1939W", 0.098, 0.732, 14.0, "1a6cb521e4b6172a20589a60a0fbe6f1"),
    # Pin the certified/highest-impulse curve.  24.12 contains three M2500T
    # curves and otherwise resolves one arbitrarily, which made OR validation
    # disagree with the Rust proxy even when both catalogs came from 24.12.
    ("AeroTech", "M2500T", 0.098, 0.751, 14.0, "2a2c010e2811015043d13e9a2a9d327d"),
    ("AeroTech", "M650W", 0.075, 0.801, 14.0, "3a09181954764615a17550782f8e91d2"),
    ("AeroTech", "M1297W", 0.075, 0.665, 14.0, "e8e2a37082a677b9964a44d92f136392"),
    ("Cesaroni Technology Inc.", "9977M2245-P", 0.075, 1.025, 0.0, "8b5df94e82762aba9b91cd3efa75da82"),
    # Classe N (10240-20480 Ns) - MONSTROS
    ("AeroTech", "N2000W", 0.098, 1.046, 14.0, "f097ce447c4666d6124670f2768d667a"),
    ("AeroTech", "N4800T", 0.098, 1.194, 14.0, "72eef3815cf5cd203bd881bbce613ea7"),
    ("Cesaroni Technology Inc.", "20146N5800-P", 0.098, 1.239, 0.0, "f63bdcd39eb397ae77483e657eea6090"),
    # 40960O8000-P: real OpenRocket catalog diameter is 161mm, not the
    # 150mm here -- a PRIOR, deliberate correction (see this table's own
    # header comment: "the real motor is 150mm"), unrelated to and
    # unaffected by adding this digest. The digest-matching path above
    # resolves this motor's THRUST CURVE correctly regardless of the
    # pre-existing, intentional diameter divergence.
    ("Cesaroni Technology Inc.", "40960O8000-P", 0.150, 0.957, 0.0, "d925d384c9082af5bacb8a64643723cf"),
    # Long-burn 38 mm curves selected from the bundled OpenRocket 24.12 DB
    # for the subsonic OSIFOG precision mission.  Appended to preserve every
    # historical numeric index above.
    ("Cesaroni Technology Inc.", "644J94-P", 0.038, 0.367, 10.0,
     "9d3b2a0bbc3b1524571d426ade3c235f"),
    ("Cesaroni Technology Inc.", "949J150-P", 0.038, 0.500, 10.0,
     "2a49c07ae36061aedd2af01c57a83d81"),
    # --- Low-thrust long-burn retro family (appended 2026-07-25) ------------
    # Sourced programmatically from OpenRocket 24.12's own live motor database
    # (scripts/dump_or_motor_catalog.py -> or_motor_catalog.json); digests are
    # machine-copied, never hand-transcribed.
    #
    # Rationale: every candidate through I brakes with a retro whose
    # thrust-to-weight at touchdown is ~6.  The touchdown speed then slews at
    # ~5 g, so the legal (<5 m/s) ignition window is milliseconds wide and the
    # landing does not survive a reseed or a finer timestep.  These four motors
    # put T/W in the 1.2-1.7 band, which drops the terminal slew rate to
    # 0.2-0.7 g and widens the predicted window by two to three orders of
    # magnitude.  All four carry delays=[inf] in the live database, i.e. they
    # are plugged by construction with no ejection charge -- which is what a
    # retro mount requires anyway.
    ("AeroTech", "I49N", 0.038, 0.184, 0.0,
     "72b175a213c87270ed10cda958d7a364"),        # 49.2 N, 7.81 s, 384 Ns
    ("AeroTech", "I59WN", 0.038, 0.232, 0.0,
     "98ae79d6746a616fdd637d75f4c97eb6"),        # 59.5 N, 8.15 s, 487 Ns
    ("Cesaroni Technology Inc.", "1211J140-P", 0.054, 0.329, 0.0,
     "64a5fcb5eb0f72de6992aad81d5f9d04"),        # 142.7 N, 8.46 s, 1211 Ns
    ("AeroTech", "J125W", 0.054, 0.368, 0.0,
     "30fa5a3b18fdd4a4165c7922172f9b54"),        # 129.1 N, 9.90 s, 1280 Ns
    # Sustainer-class long burns.  2285K260-P is a near drop-in for K700W --
    # same 54 mm case, 0.572 m vs 0.568 m, 2.047 kg vs 2.035 kg loaded, 2282 Ns
    # vs 2284 Ns -- but delivers it as 268 N over 8.51 s instead of 689 N over
    # 3.30 s.  Because the loaded mass matches, the ascent and apogee solution
    # carries over unchanged while the landing thrust ratio drops from 6.6 to
    # 2.6.  2645L265-P trades a 0.45 kg mass increase for a closer impulse
    # match (+2% vs -12%).
    ("Cesaroni Technology Inc.", "2285K260-P", 0.054, 0.572, 0.0,
     "e3374067680f190f169a0cee3da14c7f"),        # 268.0 N, 8.51 s, 2282 Ns
    ("Cesaroni Technology Inc.", "2645L265-P", 0.054, 0.649, 0.0,
     "608f3ec9b9c568c127227b507b39779e"),        # 265.3 N, 9.90 s, 2646 Ns
    # Appended (never inserted) so every index above stays stable -- the
    # verified I49N=38 Booster window result is addressed by index.
    ("AeroTech", "I40N-P", 0.038, 0.203, 0.0,
     "0f31632f7504a44cc499c213a298d389"),        # 38.1 N, 9.91 s, 377 Ns
    # Lowest-thrust 75 mm motor that still carries enough impulse to arrest the
    # ~11 kg Sustainer from ~176 m/s.  Its 435 N against K700W's 689 N drops the
    # landing thrust ratio from 6.6 to ~3.7, halving the rate at which touchdown
    # speed slews with ignition error -- which is what limits seed pass rate.
    ("Gorilla Rocket Motors", "L425WC", 0.075, 0.497, 0.0,
     "4b457c742b45e42fb683b93b7860cc32"),        # 434.9 N, 7.75 s, 3375 Ns
    # --- Two-burn Sustainer family (appended 2026-07-25) --------------------
    # The Sustainer cannot be landed by a single motor: at 10.65 kg and 176 m/s
    # terminal, a thrust-ratio-1.4 arrest needs ~4200 Ns, and nothing that fits
    # carries it.  Splitting the job removes that wall.  Phase 1 is a hard,
    # short brake from the (currently empty) 3-motor octaweb -- its own timing
    # precision does not matter, because the stage enters it at terminal
    # velocity regardless of when it fires, so its EXIT SPEED is fixed and only
    # the exit ALTITUDE moves.  Phase 2 is then a low-thrust central arrest from
    # that much lower speed, which is the regime where the Booster's I49N
    # reaches 83% seed pass.
    #
    # 38 mm ceiling: with a 54 mm central sleeve at body radius 0.082 m the cage
    # admits at most a 46.2 mm main; with the smaller 54 mm-class centrals below
    # the 38 mm entries here all clear it with ~9 mm of sleeve wall.
    #
    # Phase-1 (octaweb, 38 mm) candidates -- high thrust, short burn:
    ("Loki Research", "I426LB", 0.038, 0.307, 0.0,
     "d7f519c49d122f3a6a3a2266989a27a9"),        # 428.5 N, 1.18 s, 509 Ns
    ("AeroTech", "I350R", 0.038, 0.356, 0.0,
     "b366be4ca783ba14282729d134aab498"),        # 408.4 N, 1.58 s, 644 Ns
    ("AeroTech", "J350W", 0.038, 0.337, 0.0,
     "3425c0019512b8c67b90eac455bda968"),        # 445.4 N, 1.49 s, 669 Ns
    ("AeroTech", "J510W", 0.038, 0.584, 0.0,
     "0267bc4e1d441092f4fd0b252daf0c36"),        # 545.9 N, 2.15 s, 1180 Ns
    ("Loki Research", "J1026CT", 0.038, 0.625, 0.0,
     "1065189a3f6aaaf23909eb04dfb22c0c"),        # 1038.8 N, 1.21 s, 1264 Ns
    ("West Coast Hybrids", "499I110-P", 0.038, 0.606, 0.0,
     "2cebec9238267c4d0f32329d8da9a8b6"),        # 116.0 N, 3.88 s, 450 Ns
    # Phase-2 (central, 54 mm) candidates -- the T/W 1.4-2.6 band against a
    # ~10 kg stage that has already been slowed to 30-100 m/s:
    ("AeroTech", "J99N", 0.054, 0.231, 0.0,
     "052c36d57bd7b25348e59f4803d8c4ff"),        # 96.0 N, 9.68 s, 930 Ns
    ("Gorilla Rocket Motors", "J167WC", 0.054, 0.326, 0.0,
     "57109ef6db4fdbb135bcab781855107e"),        # 163.4 N, 5.83 s, 956 Ns
    ("AeroTech", "K185W", 0.054, 0.437, 0.0,
     "03afbcb8e1394719f45d25c39bc21c47"),        # 193.5 N, 7.10 s, 1379 Ns
    ("Gorilla Rocket Motors", "K222WC", 0.054, 0.402, 0.0,
     "2fac79db7c52948f60b7dd0d431f4358"),        # 208.2 N, 6.21 s, 1298 Ns
    ("AeroTech", "K270W", 0.054, 0.579, 0.0,
     "b4dfe26dd87fdc90d485242e213e8817"),        # 244.8 N, 8.02 s, 1970 Ns
    ("Cesaroni Technology Inc.", "2021K261-P", 0.054, 0.488, 0.0,
     "fc5660f29faf70e238486be372c1eddf"),        # 259.4 N, 7.79 s, 2029 Ns
    ("AeroTech", "K250W", 0.054, 0.673, 0.0,
     "82fc51183f7423ed72e47ffb2cb84ddf"),        # 273.4 N, 9.31 s, 2553 Ns
    # Ultra-long burns: 65-67 N for 20+ s.  Too weak to hold a 10 kg stage
    # alone (T/W 0.66) but the only motors that can arrest a light one gently.
    ("AeroTech", "K62N", 0.054, 0.274, 0.0,
     "56d503ed3e05031887c99cfb88a0272d"),        # 65.1 N, 21.57 s, 1406 Ns
    ("AeroTech", "K76WN-P", 0.054, 0.368, 0.0,
     "b915d60787241c5de017d019f0265385"),        # 66.9 N, 20.27 s, 1358 Ns
    # --- Sustainer mid-thrust-ratio band (appended 2026-07-25) --------------
    # Every Sustainer retro tried so far sits at one of two extremes against
    # the 11.93 kg braking mass: T/W ~2.3 (2285K260-P, 2645L265-P), which is
    # under-impulsed and floors at 11-35 m/s, or T/W ~5.9 (K700W), which
    # arrests fine but slews touchdown speed at 48 m/s^2 for a ~12 ms window.
    # Nothing had been tried between them.  These three sit at T/W 3.6-4.7
    # while carrying essentially K700W's impulse and loaded mass, so they are
    # drop-in swaps that leave the ascent and apogee solution intact.
    ("AeroTech", "K375NW", 0.054, 0.579, 0.0,
     "3d02ca84a52e17810a8e2fe9a9c4053b"),        # 426.4 N, 5.17 s, 2230 Ns
    ("Loki Research", "K527LR", 0.054, 0.492, 0.0,
     "ddd49d7ce30d4e450d3cb6f9bb7b527d"),        # 528.6 N, 3.77 s, 2001 Ns
    ("AeroTech", "K480W", 0.054, 0.579, 0.0,
     "29901e68bb1b086809b21978a1776a3b"),        # 554.2 N, 4.12 s, 2295 Ns
]

# =========================================================================
# BANCO DE MATERIAIS (OpenRocket built-in)
# =========================================================================
MATERIALS = {
    "fiberglass":  ("Fiberglass",  "bulk", 1850.0),
    "carbon":      ("Carbon fiber","bulk", 1780.0),
    "cardboard":   ("Cardboard",   "bulk", 680.0),
    "pla":         ("PLA",         "bulk", 1250.0),
    "birch":       ("Birch",       "bulk", 670.0),
    "balsa":       ("Balsa",       "bulk", 170.0),
    "aluminum":    ("Aluminum",    "bulk", 2700.0),
    "kraft":       ("Kraft phenolic","bulk",950.0),
    "abs":         ("ABS",         "bulk", 1050.0),
    "polycarbonate": ("Polycarbonate","bulk",1200.0),
    "steel":       ("Steel",       "bulk", 7850.0),
    "lead":        ("Lead",        "bulk", 11340.0),
}

NOSE_SHAPES = ["ogive", "conical", "power", "parabolic", "haack"]
FIN_CROSS_SECTIONS = ["square", "rounded", "airfoil", "double-wedge"]

class RocketArchitect:
    """Constrói um foguete inteiro do zero via XML puro."""
    
    def __init__(self):
        self.config_id = str(uuid.uuid4())
    
    def _mat_xml(self, mat_key):
        name, mtype, density = MATERIALS.get(mat_key, MATERIALS["cardboard"])
        return f'<material type="{mtype}" density="{density}">{name}</material>'
    

    def _build_stage(self, params, name="Sustainer", prefix=""):
        motor_idx = params[f"{prefix}motor_index"]
        motor = MOTOR_DATABASE[motor_idx]
        mfr, designation, motor_diam, motor_len, delay, digest = motor
        
        # Inner tube matches motor
        motor_mount_radius = motor_diam / 2.0 + 0.001
        motor_mount_length = motor_len + 0.02
        
        # O raio do corpo agora é unificado e calculado em build()
        body_radius = params["body_radius"]
        base_body_thickness = params.get(f"{prefix}body_thickness", params.get("body_thickness", 0.002))
        
        # Geometria das aletas
        sweep_rad = math.radians(params.get(f"{prefix}fin_sweep_angle", params.get("fin_sweep_angle", 30)))
        fin_height = params.get(f"{prefix}fin_height", params.get("fin_height", 0.05))
        fin_root = params.get(f"{prefix}fin_root_chord", params.get("fin_root_chord", 0.1))
        fin_tip = params.get(f"{prefix}fin_tip_chord", params.get("fin_tip_chord", fin_root * 0.3))
        sweep_offset = fin_height * math.tan(sweep_rad)
        
        fin_points = [
            (0.0, 0.0),
            (sweep_offset, fin_height),
            (sweep_offset + fin_tip, fin_height),
            (fin_root, 0.0),
        ]
        fin_points_xml = "\n".join([f'<point x="{p[0]:.6f}" y="{p[1]:.6f}"/>' for p in fin_points])
        
        # Componentes condicionais
        has_nose = (prefix == "")  # Only Sustainer gets a nosecone
        has_chute = (prefix == "") # Only Sustainer gets the main parachute for now
        
        xml = f"""
      <stage>
        <name>{name}</name>
        <subcomponents>"""
        
        if has_nose:
            nose_len = params["nose_length"]
            nose_thick = params.get("nose_thickness", 0.002)
            xml += f"""
          <nosecone>
            <name>Nose Cone</name>
            <finish>polished</finish>
            {self._mat_xml(params.get("nose_material", "fiberglass"))}
            <length>{nose_len:.6f}</length>
            <thickness>{nose_thick:.6f}</thickness>
            <shape>{'haack' if params.get("nose_shape", "ogive") == 'vonkarman' else params.get("nose_shape", "ogive")}</shape>
            <shapeclipped>false</shapeclipped>
            <shapeparameter>0.0</shapeparameter>
            <aftradius>auto</aftradius>
            <aftshoulderlength>0.03</aftshoulderlength>
            <aftshoulderradius>{body_radius - base_body_thickness:.6f}</aftshoulderradius>
            <aftshoulderthickness>{nose_thick:.6f}</aftshoulderthickness>
            <aftshouldercapped>false</aftshouldercapped>
          </nosecone>"""

        base_body_len = params.get(f"{prefix}body_length", params.get("body_length", 0.5))
        # O tubo não pode ser menor que o motor mount que vai dentro dele!
        body_len = max(base_body_len, motor_mount_length)
        xml += f"""
          <bodytube>
            <name>Airframe {name}</name>
            <finish>polished</finish>
            {self._mat_xml(params.get(f"{prefix}body_material", params.get("body_material", "kraft")))}
            <length>{body_len:.6f}</length>
            <thickness>{base_body_thickness:.6f}</thickness>
            <radius>{body_radius:.6f}</radius>
            <subcomponents>
              <innertube>
                <name>Motor Mount</name>
                <position type="bottom">0.005</position>
                {self._mat_xml("kraft")}
                <length>{motor_mount_length:.6f}</length>
                <radialposition>0.0</radialposition>
                <radialdirection>0.0</radialdirection>
                <outerradius>{motor_mount_radius:.6f}</outerradius>
                <thickness>0.001</thickness>
                <clusterconfiguration>single</clusterconfiguration>
                <clusterscale>1.0</clusterscale>
                <clusterrotation>0.0</clusterrotation>
                <motormount>
                  <ignitionevent>{'automatic' if prefix == 'booster_' else params.get('sustainer_ignition_event', 'automatic')}</ignitionevent>
                  <ignitiondelay>{params.get('ignition_delay', 0.0) if prefix == '' else 0.0}</ignitiondelay>
                  <overhang>0.005</overhang>
                  <motor configid="{self.config_id}">
                    <manufacturer>{mfr}</manufacturer>
                    {f'<digest>{digest}</digest>' if digest else ''}
                    <designation>{designation}</designation>
                    <diameter>{motor_diam}</diameter>
                    <length>{motor_len}</length>
                    <delay>{delay}</delay>
                  </motor>
                </motormount>
                <subcomponents>
                  <centeringring>
                    <name>Aft Centering Ring</name>
                    <position type="bottom">-0.005</position>
                    {self._mat_xml("birch")}
                    <length>0.005</length>
                    <radialposition>0.0</radialposition>
                    <radialdirection>0.0</radialdirection>
                    <outerradius>{body_radius - base_body_thickness:.6f}</outerradius>
                    <innerradius>{motor_mount_radius:.6f}</innerradius>
                  </centeringring>
                  <centeringring>
                    <name>Forward Centering Ring</name>
                    <position type="top">0.005</position>
                    {self._mat_xml("birch")}
                    <length>0.005</length>
                    <radialposition>0.0</radialposition>
                    <radialdirection>0.0</radialdirection>
                    <outerradius>{body_radius - base_body_thickness:.6f}</outerradius>
                    <innerradius>{motor_mount_radius:.6f}</innerradius>
                  </centeringring>
                </subcomponents>
              </innertube>"""
              
        if has_chute:
            xml += f"""
              <parachute>
                <name>Recovery Chute</name>
                <position type="middle">0.0</position>
                <packedlength>0.06</packedlength>
                <packedradius>{body_radius * 0.6:.6f}</packedradius>
                <radialposition>0.0</radialposition>
                <radialdirection>0.0</radialdirection>
                <cd>auto</cd>
                <material type="surface" density="0.067">Ripstop nylon</material>
                <deployevent>apogee</deployevent>
                <deployaltitude>300.0</deployaltitude>
                <deploydelay>{params.get("chute_deploy_delay", 0.0):.1f}</deploydelay>
                <diameter>{params.get("chute_diameter", 0.5):.4f}</diameter>
                <linecount>6</linecount>
                <linelength>{params.get("chute_diameter", 0.5) * 1.1:.4f}</linelength>
                <linematerial type="line" density="0.001">Braided nylon (2 mm, 1/16 in)</linematerial>
              </parachute>"""

        if params.get(f"{prefix}payload_mass", 0.0) > 0.0:
            mass_val = params[f"{prefix}payload_mass"]
            xml += f"""
              <masscomponent>
                <name>Tuning Payload</name>
                <position type="top">0.05</position>
                <packedlength>0.05</packedlength>
                <packedradius>{body_radius * 0.8:.6f}</packedradius>
                <radialposition>0.0</radialposition>
                <radialdirection>0.0</radialdirection>
                <mass>{mass_val:.6f}</mass>
                <masscomponenttype>mass</masscomponenttype>
              </masscomponent>"""

        xml += f"""
              <freeformfinset>
                <name>Fins</name>
                <position type="bottom">-0.005</position>
                <finish>polished</finish>
                {self._mat_xml(params.get(f"{prefix}fin_material", params.get("fin_material", "fiberglass")))}
                <fincount>{params.get(f"{prefix}fin_count", params.get("fin_count", 4))}</fincount>
                <rotation>0.0</rotation>
                <thickness>{params.get(f"{prefix}fin_thickness", params.get("fin_thickness", 0.003)):.6f}</thickness>
                <crosssection>{params.get(f"{prefix}fin_cross_section", params.get("fin_cross_section", "airfoil"))}</crosssection>
                <cant>{params.get(f"{prefix}fin_cant", params.get("fin_cant", 0.0)):.2f}</cant>
                <filletradius>0.003</filletradius>
                <filletmaterial type="bulk" density="1250.0">Epoxy</filletmaterial>
                <finpoints>
                  {fin_points_xml}
                </finpoints>
              </freeformfinset>
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>"""
        return xml

    def build(self, params):
        designation_sus = MOTOR_DATABASE[params["motor_index"]][1]
        designation_boost = MOTOR_DATABASE[params["booster_motor_index"]][1] if "booster_motor_index" in params else None
        
        # Calcular raio de corpo global unificado
        # Motor 1 (Sustainer)
        m1_diam = MOTOR_DATABASE[params["motor_index"]][2]
        m1_thick = params.get("body_thickness", 0.002)
        r1 = (m1_diam / 2.0) + 0.001 + m1_thick + 0.002
        
        # Motor 2 (Booster) se existir
        r2 = 0
        if "booster_motor_index" in params:
            m2_diam = MOTOR_DATABASE[params["booster_motor_index"]][2]
            m2_thick = params.get("booster_body_thickness", 0.002)
            r2 = (m2_diam / 2.0) + 0.001 + m2_thick + 0.002
            
        base_body_radius = params.get("body_radius", 0.02)
        params["body_radius"] = max(base_body_radius, r1, r2)
        
        name_str = f"L2 Forge - {designation_sus}" + (f" + {designation_boost}" if designation_boost else "")
        team_str = "L2 Systems 1024" if params.get("is_final", False) else "L2 Systems AI"

        # Header
        xml = f"""<?xml version="1.0" ?>
<openrocket version="1.6" creator="L2-Systems-Forge-v3">
  <rocket>
    <name>{name_str}</name>
    <designer>{team_str}</designer>
    <motorconfiguration configid="{self.config_id}" default="true"/>
    <referencetype>maximum</referencetype>
    <subcomponents>"""
        
        # Sustainer (Top stage)
        # If it's multi-stage, sustainer shouldn't ignite automatically
        if "booster_motor_index" in params:
            params['sustainer_ignition_event'] = 'automatic'  # Or 'ignitiondelay'
            
        xml += self._build_stage(params, name="Sustainer", prefix="")
        
        # Booster (Bottom stage)
        if "booster_motor_index" in params:
            xml += self._build_stage(params, name="Booster", prefix="booster_")

        # Footer
        xml += f"""
    </subcomponents>
  </rocket>
  <simulations>
    <simulation status="notsimulated">
      <name>L2 Forge Simulation</name>
      <simulator>RK4Simulator</simulator>
      <calculator>BarrowmanCalculator</calculator>
      <conditions>
        <configid>{self.config_id}</configid>
        <launchrodlength>2.0</launchrodlength>
        <launchrodangle>{math.radians(params.get("launch_angle", 0.0)):.6f}</launchrodangle>
        <launchroddirection>90.0</launchroddirection>
        <windaverage>2.0</windaverage>
        <windturbulence>0.1</windturbulence>
        <launchaltitude>0.0</launchaltitude>
        <launchlatitude>-23.55</launchlatitude>
        <launchlongitude>-46.63</launchlongitude>
        <geodeticmethod>spherical</geodeticmethod>
        <atmosphere model="isa"/>
        <timestep>0.05</timestep>
      </conditions>
    </simulation>
  </simulations>
</openrocket>
"""
        return xml

    def save(self, params, filepath):
        xml = self.build(params)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(xml)
        return filepath


def random_rocket_params(motor_class_min=14, motor_class_max=None):
    """Gera parâmetros aleatórios para um foguete válido."""
    if motor_class_max is None:
        motor_class_max = len(MOTOR_DATABASE) - 1
    
    motor_idx = random.randint(motor_class_min, motor_class_max)
    motor = MOTOR_DATABASE[motor_idx]
    motor_diam = motor[2]
    
    # Body radius precisa acomodar o motor
    min_body_radius = motor_diam / 2 + 0.005
    body_radius = random.uniform(min_body_radius, min_body_radius + 0.025)
    
    return {
        "motor_index": motor_idx,
        "nose_shape": random.choice(NOSE_SHAPES),
        "nose_length": random.uniform(0.15, 0.45),
        "nose_material": random.choice(["fiberglass", "carbon", "pla", "abs"]),
        "nose_thickness": random.uniform(0.0015, 0.004),
        "body_radius": body_radius,
        "body_length": random.uniform(0.40, 1.20),
        "body_thickness": random.uniform(0.001, 0.003),
        "body_material": random.choice(["kraft", "fiberglass", "carbon", "cardboard"]),
        "fin_count": random.choice([3, 4, 6]),
        "fin_root_chord": random.uniform(0.06, 0.20),
        "fin_tip_chord": random.uniform(0.02, 0.08),
        "fin_height": random.uniform(0.04, 0.12),
        "fin_sweep_angle": random.uniform(15, 55),
        "fin_thickness": random.uniform(0.001, 0.004),
        "fin_material": random.choice(["fiberglass", "carbon", "pla", "birch", "balsa"]),
        "fin_cross_section": random.choice(FIN_CROSS_SECTIONS),
        "fin_cant": random.uniform(0.0, 3.0),
        "chute_diameter": random.uniform(0.3, 1.0),
        "chute_deploy_delay": random.uniform(0.0, 3.0),
        "launch_angle": 0.0,
    }


def mutate_params(params, rate=0.20):
    """Muta parâmetros de um foguete existente."""
    new = dict(params)
    
    # Motor: chance de upgrade/downgrade
    if random.random() < rate:
        delta = random.choice([-2, -1, 1, 2, 3])
        new["motor_index"] = max(0, min(len(MOTOR_DATABASE)-1, new["motor_index"] + delta))
    
    # Nose
    if random.random() < rate:
        new["nose_shape"] = random.choice(NOSE_SHAPES)
    if random.random() < rate:
        new["nose_length"] *= random.uniform(0.8, 1.3)
    if random.random() < rate:
        new["nose_material"] = random.choice(list(MATERIALS.keys()))
    
    # Body
    if random.random() < rate:
        new["body_length"] *= random.uniform(0.85, 1.2)
    if random.random() < rate:
        new["body_radius"] *= random.uniform(0.9, 1.15)
    if random.random() < rate:
        new["body_thickness"] *= random.uniform(0.8, 1.3)
    if random.random() < rate:
        new["body_material"] = random.choice(list(MATERIALS.keys()))
    
    # Fins
    if random.random() < rate:
        new["fin_count"] = random.choice([3, 4, 6])
    if random.random() < rate:
        new["fin_root_chord"] *= random.uniform(0.8, 1.3)
    if random.random() < rate:
        new["fin_tip_chord"] *= random.uniform(0.7, 1.4)
    if random.random() < rate:
        new["fin_height"] *= random.uniform(0.8, 1.3)
    if random.random() < rate:
        new["fin_sweep_angle"] = max(5, min(60, new["fin_sweep_angle"] + random.uniform(-10, 10)))
    if random.random() < rate:
        new["fin_thickness"] *= random.uniform(0.7, 1.4)
    if random.random() < rate:
        new["fin_material"] = random.choice(list(MATERIALS.keys()))
    if random.random() < rate:
        new["fin_cross_section"] = random.choice(FIN_CROSS_SECTIONS)
    if random.random() < rate:
        new["fin_cant"] = max(0, min(5, new["fin_cant"] + random.uniform(-1, 1)))
    
    # Chute
    if random.random() < rate:
        new["chute_diameter"] *= random.uniform(0.8, 1.3)
    
    return new


def crossover(parent_a, parent_b):
    """Crossover entre dois conjuntos de parâmetros."""
    child = {}
    for key in parent_a:
        child[key] = parent_a[key] if random.random() < 0.5 else parent_b[key]
    return child


if __name__ == "__main__":
    # Teste: gera um foguete com motor Classe I
    architect = RocketArchitect()
    params = random_rocket_params(motor_class_min=14, motor_class_max=18)
    params["launch_angle"] = 0.0
    architect.save(params, "designs/optimized/L2_Forge_Test.ork")
    print("[!] Foguete de teste gerado: designs/optimized/L2_Forge_Test.ork")
    print(f"    Motor: {MOTOR_DATABASE[params['motor_index']][1]}")
    print(f"    Nose: {params['nose_shape']} ({params['nose_length']*100:.1f}cm)")
    print(f"    Body: {params['body_length']*100:.1f}cm x {params['body_radius']*200:.1f}mm diam")
    print(f"    Fins: {params['fin_count']}x {params['fin_cross_section']}")
