import net.sf.openrocket.document.OpenRocketDocument;
import net.sf.openrocket.file.GeneralRocketLoader;
import net.sf.openrocket.rocketcomponent.Rocket;
import net.sf.openrocket.rocketcomponent.FlightConfiguration;
import net.sf.openrocket.aerodynamics.BarrowmanCalculator;
import net.sf.openrocket.aerodynamics.FlightConditions;
import net.sf.openrocket.aerodynamics.AerodynamicForces;
import net.sf.openrocket.logging.WarningSet;
import java.io.File;

import com.google.inject.Guice;
import com.google.inject.Injector;
import net.sf.openrocket.startup.Application;
import net.sf.openrocket.startup.GuiModule;

public class PrintCD {
    public static void main(String[] args) throws Exception {
        Injector injector = Guice.createInjector(new GuiModule());
        Application.setInjector(injector);
        
        GeneralRocketLoader loader = new GeneralRocketLoader(new File("l2_engine/tests/fixtures/L2_Hyper_Parallel_15K.ork"));
        OpenRocketDocument doc = loader.load();
        Rocket rocket = doc.getRocket();
        FlightConfiguration config = rocket.getSelectedConfiguration();
        BarrowmanCalculator calc = new BarrowmanCalculator();
        WarningSet warnings = new WarningSet();
        
        System.out.println("Mach | Total CD | Friction CD | Base CD | Pressure CD");
        for (double mach = 1.0; mach <= 6.0; mach += 1.0) {
            FlightConditions conditions = new FlightConditions(config);
            conditions.setMach(mach);
            conditions.setVelocity(mach * 340.0); // approximate
            conditions.setAOA(0.0);
            
            AerodynamicForces forces = calc.getAerodynamicForces(config, conditions, warnings);
            System.out.printf("%.1f  | %.4f   | %.4f      | %.4f  | %.4f%n",
                mach,
                forces.getCD(),
                forces.getFrictionCD(),
                forces.getBaseCD(),
                forces.getPressureCD());
        }
    }
}
