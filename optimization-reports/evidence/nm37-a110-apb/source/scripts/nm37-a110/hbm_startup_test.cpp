// Digital two-clock protocol regression for the production HBM startup/gate RTL.
// The memory hard macro is outside this test: APB_COMPLETE is driven explicitly.
#include "VHBMStartupVerificationHarness.h"
#include "verilated.h"
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

static uint64_t checks = 0, gateVectors = 0, scenarioCount = 0;
static uint64_t shortCompleteFaults = 0, partialInitializationResets = 0;
static uint64_t pllScenarios = 0, pllReleaseEvents = 0;
static constexpr unsigned pllStableCycles = 7;
static uint64_t randomState = 0x98be271ac6048d31ULL;
static uint32_t random32() {
    randomState ^= randomState << 13;
    randomState ^= randomState >> 7;
    randomState ^= randomState << 17;
    return uint32_t(randomState);
}

template <size_t N> static void randomWide(VlWide<N>& value, unsigned width) {
    for (size_t i = 0; i < N; ++i) value[i] = random32();
    if (width % 32) value[N - 1] &= (uint32_t(1) << (width % 32)) - 1;
}

struct Scenario {
    unsigned axiHalf, apbHalf, phase;
};

class Test {
    std::unique_ptr<VHBMStartupVerificationHarness> dut;
    Scenario clocks;
    uint64_t time = 0, nextAxi, nextApb;
    unsigned apbReleaseEdges = 0, axiReleaseEdges = 0;
    unsigned completeAxiEdges = 0;
    bool sampledComplete = false;
    bool runAxi = true, runApb = true;

    void require(bool ok, const std::string& message) const {
        ++checks;
        if (!ok) throw std::runtime_error("scenario " + std::to_string(scenarioCount) +
            ", tick " + std::to_string(time) + ": " + message);
    }

    void checkGate() {
        const unsigned expected = dut->io_trafficReady ? dut->io_controls : 0;
        require(dut->io_observed == expected,
                "AXI valid/ready leaked while blocked or changed while enabled");
        if (dut->io_trafficReady)
            require(dut->io_payloadMatch, "AXI payload field changed through enabled gate");
    }

    void eval(bool axiRise = false, bool apbRise = false) {
        const bool oldApbResetN = dut->io_apbResetN;
        const bool oldAxiResetN = dut->io_axiResetN;
        const bool oldReady = dut->io_trafficReady;
        const bool oldFault = dut->io_initializationFault;
        if (dut->io_reset) {
            apbReleaseEdges = axiReleaseEdges = completeAxiEdges = 0;
            sampledComplete = false;
        } else {
            if (apbRise) ++apbReleaseEdges;
            if (!oldApbResetN) axiReleaseEdges = 0;
            else if (axiRise) ++axiReleaseEdges;
            // The input represents a level from the APB domain. No readiness is
            // permitted before a valid APB sample and three destination edges.
            if (apbRise && oldApbResetN && dut->io_apbComplete) sampledComplete = true;
            if (sampledComplete && axiRise) ++completeAxiEdges;
        }
        dut->eval();
        if (dut->io_reset) {
            require(!dut->io_apbResetN && !dut->io_axiResetN,
                    "common reset must assert both resets without waiting for clocks");
            require(!dut->io_trafficReady && !dut->io_initializationFault,
                    "common reset must immediately clear readiness and sticky fault");
        }
        if (!oldApbResetN && dut->io_apbResetN) {
            require(apbRise, "APB reset release was not on an APB rising edge");
            require(apbReleaseEdges >= 3, "APB reset released before three local edges");
        }
        if (!oldAxiResetN && dut->io_axiResetN) {
            require(axiRise, "AXI reset release was not on an AXI rising edge");
            require(oldApbResetN, "AXI reset released before the APB domain reset");
            require(axiReleaseEdges >= 3, "AXI reset released before three eligible local edges");
        }
        if (!oldReady && dut->io_trafficReady) {
            require(axiRise, "traffic opened away from an AXI rising edge");
            require(sampledComplete && completeAxiEdges >= 3,
                    "traffic opened before APB sampling and destination synchronization");
        }
        if (dut->io_trafficReady)
            require(dut->io_apbResetN && dut->io_axiResetN && !dut->io_initializationFault,
                    "ready asserted before reset release or during a fault");
        if (!oldFault && dut->io_initializationFault)
            require(axiRise, "fault asserted away from an AXI rising edge");
        if (oldFault && !dut->io_reset)
            require(dut->io_initializationFault && !dut->io_trafficReady,
                    "fail-stop fault cleared without a common reset");
        checkGate();
    }

    void tick() {
        ++time;
        bool axiRise = false, apbRise = false;
        if (time == nextAxi) {
            nextAxi += clocks.axiHalf;
            if (runAxi) { dut->io_axiClock ^= 1; axiRise = dut->io_axiClock; }
        }
        if (time == nextApb) {
            nextApb += clocks.apbHalf;
            if (runApb) { dut->io_apbClock ^= 1; apbRise = dut->io_apbClock; }
        }
        eval(axiRise, apbRise);
    }

    void cycles(unsigned count) {
        const uint64_t ticks = uint64_t(count) * 2 * std::max(clocks.axiHalf, clocks.apbHalf);
        for (uint64_t i = 0; i < ticks; ++i) tick();
    }

    void edges(unsigned count, bool apb) {
        require(apb ? runApb : runAxi, "cannot wait for a stopped clock");
        while (count) {
            const bool old = apb ? dut->io_apbClock : dut->io_axiClock;
            tick();
            const bool now = apb ? dut->io_apbClock : dut->io_axiClock;
            if (!old && now) --count;
        }
    }

    void reset(bool value) { dut->io_reset = value; eval(); }

    void complete(bool value) {
        const bool oldReady = dut->io_trafficReady;
        const bool oldFault = dut->io_initializationFault;
        dut->io_apbComplete = value;
        eval();
        require(dut->io_trafficReady == oldReady && dut->io_initializationFault == oldFault,
                "APB_COMPLETE bypassed synchronization combinationally");
    }

    void randomPayload() {
        randomWide(dut->io_aw, 65);
        randomWide(dut->io_w, 289);
        dut->io_b = random32() & 0xff;
        randomWide(dut->io_ar, 65);
        randomWide(dut->io_r, 265);
    }

    void truthTable(bool ready) {
        require(bool(dut->io_trafficReady) == ready, "unexpected state at gate truth-table test");
        // Every combination of all ten valid/ready inputs, without clock edges:
        // this also verifies that enabling the gate adds no pipeline stage.
        for (unsigned vector = 0; vector < 1024; ++vector) {
            dut->io_controls = vector;
            randomPayload();
            eval();
            ++gateVectors;
        }
        dut->io_controls = 1023;
        eval();
    }

    void awaitReady() {
        for (unsigned i = 0; i < 24 && !dut->io_trafficReady; ++i) cycles(1);
        require(dut->io_trafficReady, "stable initialization did not eventually enable traffic");
    }

    void coldStart() {
        complete(false);
        reset(true);
        cycles(4);
        reset(false);
        cycles(12);
        require(dut->io_apbResetN && dut->io_axiResetN, "local resets failed to release");
        require(!dut->io_trafficReady && !dut->io_initializationFault,
                "traffic or fault before initial initialization completion");
    }

public:
    explicit Test(Scenario s) : dut(new VHBMStartupVerificationHarness), clocks(s),
        nextAxi(s.axiHalf + s.phase), nextApb(s.apbHalf) {
        ++scenarioCount;
        dut->io_axiClock = dut->io_apbClock = 0;
        dut->io_reset = dut->io_apbComplete = 0;
        dut->io_pllMonitorClock = dut->io_pllExternalReset = 0;
        dut->io_pllRawReset = 1;
        dut->io_controls = 1023;
        randomPayload();
        dut->eval();
        reset(true);
    }

    void run() {
        // APB stopped during cold start: the independent AXI clock must not
        // release the stack or consume a request, even with a stale high level.
        runApb = false;
        complete(true);
        reset(false);
        cycles(12);
        require(!dut->io_apbResetN && !dut->io_axiResetN && !dut->io_trafficReady,
                "APB clock absence was bypassed during startup");
        complete(false);
        runApb = true;
        cycles(12);
        require(dut->io_apbResetN && dut->io_axiResetN, "resets did not release after APB resumed");
        truthTable(false);

        // Arbitrarily long initialization wait and short unsampled APB pulses.
        cycles(40);
        require(!dut->io_trafficReady && !dut->io_initializationFault,
                "initialization delay was replaced with an unsafe fixed timeout");
        runApb = false;
        complete(true);
        cycles(6);
        require(!dut->io_trafficReady, "completion bypassed the APB source register");
        complete(false);
        runApb = true;
        cycles(6);
        require(!dut->io_trafficReady, "unsampled completion pulse opened traffic");

        // Completion while the AXI clock is paused cannot release the gate.
        runAxi = false;
        complete(true);
        cycles(6);
        require(!dut->io_trafficReady, "completion bypassed destination clock synchronization");
        runAxi = true;
        awaitReady();
        truthTable(true);

        // Exercise live backpressure with independent clocks and payloads.
        // Legal pending AW/W/AR requests stay visible while the peer stalls;
        // the combinational gate must retain payload and valid without delay.
        dut->io_controls = 0x007;
        for (unsigned i = 0; i < 8; ++i) cycles(1);
        dut->io_controls = 0x3ff;
        cycles(8);
        // Independently stall return channels at the upstream receiver. The
        // payload stays constant while B/R valid remain asserted, then both
        // ready signals reach the memory side in the same combinational step.
        dut->io_controls = 0x300;
        cycles(8);
        dut->io_controls = 0x318;
        eval();
        cycles(4);

        // Losing completion after ready is an explicit fail-stop, including
        // pending requests/responses. It is not a lossless transaction retry.
        complete(false);
        for (unsigned i = 0; i < 24 && !dut->io_initializationFault; ++i) cycles(1);
        require(dut->io_initializationFault && !dut->io_trafficReady,
                "completion loss did not latch the fail-stop fault");
        require(dut->io_apbResetN && dut->io_axiResetN,
                "completion loss unexpectedly reset the HBM stack locally");
        truthTable(false);
        complete(true);
        cycles(16);
        require(dut->io_initializationFault && !dut->io_trafficReady,
                "completion reassertion incorrectly recovered without common reset");

        // A sub-cycle reset pulse must work with BOTH clocks stopped. All
        // peers share this cold reset; pending transactions are discarded.
        runApb = runAxi = false;
        reset(true);
        complete(false);
        tick();
        reset(false);
        cycles(6);
        require(!dut->io_apbResetN && !dut->io_axiResetN && !dut->io_trafficReady,
                "reset deasserted while both clocks were stopped");
        runApb = true;
        cycles(6);
        require(dut->io_apbResetN && !dut->io_axiResetN,
                "AXI reset was not released by its own clock");
        runAxi = true;
        cycles(8);
        complete(true);
        awaitReady();
        truthTable(true);

        // A loss sampled for just one APB cycle must also latch fail-stop,
        // even if the external level has returned before fault is observed.
        complete(false);
        edges(1, true);
        complete(true);
        for (unsigned i = 0; i < 24 && !dut->io_initializationFault; ++i) cycles(1);
        require(dut->io_initializationFault && !dut->io_trafficReady,
                "one sampled APB cycle of completion loss did not latch fault");
        cycles(16);
        require(dut->io_initializationFault && !dut->io_trafficReady,
                "short completion loss recovered without common reset");
        ++shortCompleteFaults;

        // Interrupt initialization before the completion pipeline drains.
        coldStart();
        complete(true);
        // Stop the destination immediately; APB can sample completion but no
        // stale synchronized value may survive the following common reset.
        runAxi = false;
        cycles(4);
        runAxi = true;
        edges(1 + (scenarioCount % 2), false);
        require(!dut->io_trafficReady,
                "partial completion pipeline incorrectly enabled traffic");
        reset(true);
        complete(false);
        tick();
        reset(false);
        runAxi = true;
        cycles(20);
        require(!dut->io_trafficReady && !dut->io_initializationFault,
                "interrupted initialization leaked a stale completion/fault");
        ++partialInitializationResets;
        complete(true);
        awaitReady();

        // Normal running reset, issued at an arbitrary off-clock instant.
        dut->io_controls = 1023;
        reset(true);
        complete(false);
        tick();
        reset(false);
        cycles(16);
        require(!dut->io_trafficReady, "normal reset did not require a new initialization");
        complete(true);
        awaitReady();
        dut->final();
    }
};

class PLLTest {
    std::unique_ptr<VHBMStartupVerificationHarness> dut;
    unsigned halfPeriod, stableEdges = 0;
    uint64_t time = 0, nextEdge;
    bool runClock = false;

    void require(bool ok, const std::string& message) const {
        ++checks;
        if (!ok) throw std::runtime_error("PLL scenario " + std::to_string(pllScenarios) +
            ", tick " + std::to_string(time) + ": " + message);
    }

    void eval(bool rise = false) {
        const bool oldResetN = dut->io_pllPcieResetN;
        const bool cause = dut->io_pllRawReset || dut->io_pllExternalReset;
        if (cause) stableEdges = 0;
        else if (rise) ++stableEdges;
        dut->eval();
        require(bool(dut->io_pllPcieResetN) == (!cause && stableEdges >= pllStableCycles + 3),
                "PCIe reset must assert immediately and release after exactly 3+N stable monitor edges");
        if (!oldResetN && dut->io_pllPcieResetN) {
            require(rise, "PCIe reset released away from a monitor rising edge");
            ++pllReleaseEvents;
        }
        // The HBM clocks intentionally never run. PCIe reset qualification
        // must still progress without waiting for HBM/XDMA readiness feedback.
        require(!dut->io_trafficReady, "HBM unexpectedly became ready with both clocks stopped");
    }

    void tick() {
        ++time;
        bool rise = false;
        if (time == nextEdge) {
            nextEdge += halfPeriod;
            if (runClock) { dut->io_pllMonitorClock ^= 1; rise = dut->io_pllMonitorClock; }
        }
        eval(rise);
    }

    void cycles(unsigned count) {
        for (unsigned i = 0; i < count * 2 * halfPeriod; ++i) tick();
    }

    void edges(unsigned count) {
        require(runClock, "cannot wait for a stopped monitor clock");
        while (count) {
            const bool old = dut->io_pllMonitorClock;
            tick();
            if (!old && dut->io_pllMonitorClock) --count;
        }
    }

    void raw(bool value) { dut->io_pllRawReset = value; eval(); }
    void external(bool value) { dut->io_pllExternalReset = value; eval(); }

public:
    PLLTest(unsigned half, unsigned phase) : dut(new VHBMStartupVerificationHarness),
        halfPeriod(half), nextEdge(half + phase) {
        ++pllScenarios;
        dut->io_axiClock = dut->io_apbClock = dut->io_apbComplete = 0;
        dut->io_reset = 1;
        dut->io_controls = 0;
        dut->io_pllMonitorClock = 0;
        dut->io_pllRawReset = dut->io_pllExternalReset = 1;
        eval();
    }

    void run() {
        // Removing one cause cannot override the other; without monitor edges
        // removing both causes also cannot release PCIe reset.
        raw(false);
        cycles(4);
        external(false);
        cycles(12);
        runClock = true;
        edges(pllStableCycles + 2);
        require(!dut->io_pllPcieResetN, "qualification released one cycle early");
        edges(1);
        require(dut->io_pllPcieResetN, "qualification did not complete");
        cycles(4);
        dut->io_reset = 0;
        eval();
        dut->io_reset = 1;
        eval();

        // A sub-cycle loss of lock while monitorClock is stopped must assert
        // reset asynchronously and require complete requalification on resume.
        runClock = false;
        raw(true);
        tick();
        raw(false);
        cycles(6);
        runClock = true;
        edges(4);
        // Lose lock again partway through qualification: no elapsed count may
        // survive, even though this pulse does not contain a clock edge.
        raw(true);
        tick();
        raw(false);
        edges(pllStableCycles + 2);
        require(!dut->io_pllPcieResetN, "lost lock did not restart qualification");
        edges(1);
        require(dut->io_pllPcieResetN, "second lock qualification did not complete");

        // PERST is independent of PLL lock and restarts qualification too.
        external(true);
        cycles(2);
        external(false);
        edges(pllStableCycles + 2);
        external(true);
        tick();
        external(false);
        edges(pllStableCycles + 3);
        require(dut->io_pllPcieResetN, "PERST re-entry did not qualify from zero");

        // Interleaved causes must not release while either one remains set.
        raw(true);
        external(true);
        raw(false);
        cycles(pllStableCycles + 5);
        external(false);
        edges(pllStableCycles + 3);
        require(dut->io_pllPcieResetN, "combined causes did not recover after a full stable interval");
        dut->final();
    }
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    try {
        // First four have the production 110:55 frequency ratio and different
        // phases (including coincident edges). Others stress the earlier
        // 110:100 ratio and unrelated rational ratios/edge ordering.
        const Scenario scenarios[] = {
            {500, 1000, 0}, {500, 1000, 1}, {500, 1000, 137}, {500, 1000, 500},
            {500, 550, 499}, {31, 47, 0}, {19, 97, 7}, {59, 61, 43}
        };
        for (const auto& scenario : scenarios) Test(scenario).run();
        for (unsigned i = 0; i < 8; ++i) PLLTest(5 + i * 2, i).run();
        std::cout << "PASS: independent-clock reset release, delayed/interrupted initialization, "
                  << "fail-stop/re-entry, all five AXI channels, and PLL/PERST qualification\n";
        std::cout << "{\"passed\":true,\"clock_scenarios\":" << scenarioCount
                  << ",\"gate_vectors\":" << gateVectors << ",\"checks\":" << checks
                  << ",\"short_complete_faults\":" << shortCompleteFaults
                  << ",\"partial_initialization_resets\":" << partialInitializationResets
                  << ",\"pll_scenarios\":" << pllScenarios
                  << ",\"pll_stable_cycles\":" << pllStableCycles
                  << ",\"pll_release_events\":" << pllReleaseEvents << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
