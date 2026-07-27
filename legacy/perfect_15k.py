import os
import math
from scipy.optimize import minimize
import orhelper
from orhelper import OpenRocketInstance
from forge_mega import evaluate

# A minimal, perfectly stable, 29mm or 54mm high-power rocket XML template.
def build_ork(mass_kg):
    # Using Cesaroni N5800-P (motor designation: 20146N5800-P)
    # The rocket needs to be aerodynamic and stable.
    # We use a 4m long body tube to ensure it's stable even with the massive N5800 motor.
    # Radius: 0.0508 (10.16cm diameter)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<openrocket version="1.4" creator="L2-MIND">
  <rocket>
    <name>Precision Arrow</name>
    <motorconfiguration configid="0">
      <stage>0</stage>
    </motorconfiguration>
    <subcomponents>
      <stage>
        <name>Sustainer</name>
        <subcomponents>
          <nosecone>
            <name>Nose Cone</name>
            <length>0.6</length>
            <thickness>0.002</thickness>
            <shape>von karman</shape>
            <material type="bulk" density="1850.0">Fiberglass</material>
            <subcomponents>
              <masscomponent>
                <name>Tuning Payload</name>
                <position type="bottom">-0.1</position>
                <mass>{mass_kg:.6f}</mass>
                <masscomponenttype>mass</masscomponenttype>
              </masscomponent>
            </subcomponents>
          </nosecone>
          <bodytube>
            <name>Body Tube</name>
            <length>4.0</length>
            <thickness>0.002</thickness>
            <radius>0.054</radius>
            <material type="bulk" density="1850.0">Fiberglass</material>
            <motormount>
              <motor configid="0">
                <manufacturer>Cesaroni Technology Inc.</manufacturer>
                <designation>20146N5800-P</designation>
                <diameter>0.098</diameter>
                <length>1.0</length>
                <delay>0</delay>
              </motor>
            </motormount>
            <subcomponents>
              <freeformfinset>
                <name>Fins</name>
                <position type="bottom">0.0</position>
                <finpoints>
                  <point x="0.0" y="0.0"/>
                  <point x="0.3" y="0.15"/>
                  <point x="0.4" y="0.15"/>
                  <point x="0.4" y="0.0"/>
                </finpoints>
                <thickness>0.005</thickness>
                <material type="bulk" density="1850.0">Fiberglass</material>
                <fincount>4</fincount>
              </freeformfinset>
            </subcomponents>
          </bodytube>
        </subcomponents>
      </stage>
    </subcomponents>
  </rocket>
</openrocket>
"""

def main():
    print("[*] Starting exact 15,000.00000m physics convergence...")
    os.makedirs("temp_ork", exist_ok=True)
    
    with OpenRocketInstance("lib/OpenRocket-23.09.jar") as instance:
        orh = orhelper.Helper(instance)
        
        # Test 0kg mass to ensure it goes past 15,000m
        xml_0 = build_ork(0.0)
        with open("temp_ork/test_0.ork", "w") as f:
            f.write(xml_0)
            
        metrics_0 = evaluate(orh, "temp_ork/test_0.ork", 0.0)
        print(f"[*] Base N5800 Rocket (0kg payload) Apogee: {metrics_0['apogee']:.2f}m")
        if metrics_0['apogee'] < 15000:
            print("[!] ERROR: Rocket cannot reach 15km even at 0kg payload!")
            return

        def objective(x):
            new_val = x[0]
            if new_val < 0.0 or new_val > 500.0:
                return 999999.0
                
            xml = build_ork(new_val)
            filepath = "temp_ork/perfect_polish.ork"
            with open(filepath, "w") as f:
                f.write(xml)
                
            try:
                metrics = evaluate(orh, filepath, 0.0)
            except Exception:
                return 999999.0
                
            if metrics is None or metrics["apogee"] < 100:
                return 999999.0
                
            apogee = metrics["apogee"]
            error = abs(apogee - 15000.0)
            return error

        print("[*] Running Nelder-Mead on payload mass...")
        res = minimize(
            objective, 
            [10.0], 
            method='Nelder-Mead',
            options={'xatol': 1e-10, 'fatol': 1e-8, 'maxiter': 500, 'disp': True}
        )
        
        print("\n[*] Optimization Finished!")
        print(f"Final payload_mass: {res.x[0]:.6f}kg")
        print(f"Final error (meters): {res.fun:.8f}m")
        
        final_path = "designs/optimized/perfect_15k.ork"
        with open(final_path, "w") as f:
            f.write(build_ork(res.x[0]))
            
        final_metrics = evaluate(orh, final_path, 0.0)
        
        print("\n" + "="*50)
        print(f"FINAL MICRO-POLISHED APOGEE: {final_metrics['apogee']:.5f} m")
        print(f"TARGET APOGEE              : 15000.00000 m")
        print(f"MACH                       : {final_metrics['mach']:.3f}")
        print(f"SAVED TO                   : {final_path}")
        print("="*50)

if __name__ == "__main__":
    main()
