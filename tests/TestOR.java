import net.sf.openrocket.rocketcomponent.Transition;

public class TestOR {
    public static void main(String[] args) {
        System.out.println("HAACK shapeParam: " + Transition.Shape.HAACK.defaultParameter());
        System.out.println("CONICAL shapeParam: " + Transition.Shape.CONICAL.defaultParameter());
        System.out.println("OGIVE shapeParam: " + Transition.Shape.OGIVE.defaultParameter());
        System.out.println("ELLIPSOID shapeParam: " + Transition.Shape.ELLIPSOID.defaultParameter());
        System.out.println("POWER shapeParam: " + Transition.Shape.POWER.defaultParameter());
        System.out.println("PARABOLIC shapeParam: " + Transition.Shape.PARABOLIC.defaultParameter());
    }
}
