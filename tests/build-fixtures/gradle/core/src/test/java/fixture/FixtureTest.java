package fixture;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;
class FixtureTest {
    @Test void adds() { assertEquals(2, Calculator.add(1, 1)); }
    @Test void zero() { assertEquals(0, Calculator.add(0, 0)); }
}
