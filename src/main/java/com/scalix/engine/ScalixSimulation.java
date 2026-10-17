package com.scalix.engine;

import ch.qos.logback.classic.Level;
import com.scalix.allocation.LoadBalancer;
import com.scalix.allocation.ManagedVm;
import com.scalix.allocation.VmPool;
import com.scalix.allocation.VmState;
import com.scalix.config.SimulationConfig;
import com.scalix.metrics.DecisionRecord;
import com.scalix.metrics.RequestRecord;
import com.scalix.metrics.RunResult;
import com.scalix.metrics.TimeSeriesPoint;
import com.scalix.metrics.VmRecord;
import com.scalix.scaling.ScalingAction;
import com.scalix.scaling.ScalingContext;
import com.scalix.scaling.ScalingDecision;
import com.scalix.scaling.ScalingPolicy;
import com.scalix.scaling.Trend;
import com.scalix.scaling.VmLimits;
import com.scalix.workload.Request;
import com.scalix.workload.WorkloadSpec;
import org.cloudsimplus.allocationpolicies.VmAllocationPolicySimple;
import org.cloudsimplus.brokers.DatacenterBroker;
import org.cloudsimplus.brokers.DatacenterBrokerSimple;
import org.cloudsimplus.cloudlets.Cloudlet;
import org.cloudsimplus.cloudlets.CloudletSimple;
import org.cloudsimplus.core.CloudSimEntity;
import org.cloudsimplus.core.CloudSimPlus;
import org.cloudsimplus.core.events.SimEvent;
import org.cloudsimplus.datacenters.DatacenterSimple;
import org.cloudsimplus.hosts.Host;
import org.cloudsimplus.hosts.HostSimple;
import org.cloudsimplus.resources.Pe;
import org.cloudsimplus.resources.PeSimple;
import org.cloudsimplus.schedulers.cloudlet.CloudletSchedulerSpaceShared;
import org.cloudsimplus.util.Log;
import org.cloudsimplus.vms.Vm;
import org.cloudsimplus.vms.VmSimple;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Deque;
import java.util.List;

/**
 * Runs one (workload, policy) pair on CloudSim Plus 8.5.x and returns the raw measurements.
 *
 * <p>Architecture: CloudSim Plus simulates hosts, VMs and request execution (each request is a
 * {@link Cloudlet} on a VM with a space-shared scheduler, i.e. a FIFO queue per VM). A custom
 * {@link Controller} entity drives SCALIX on a fixed tick: it dispatches arriving requests through
 * the {@link LoadBalancer}, samples VM utilization, and every decision interval asks the
 * {@link ScalingPolicy} for a decision, which {@link VmPool} applies. Policy logic never touches
 * CloudSim Plus directly.
 */
public final class ScalixSimulation {

    /** Event tag for controller ticks; well above CloudSim Plus' own tags (max ~106 in 8.5.7). */
    static final int TICK_TAG = 920_001;

    /** Minimum time between CloudSim Plus events; finer than the default 0.1 s for timing accuracy. */
    private static final double MIN_TIME_BETWEEN_EVENTS = 0.01;

    static {
        Log.setLevel(Level.WARN);
    }

    private final String runId;
    private final SimulationConfig cfg;
    private final ScalingPolicy policy;
    private final WorkloadSpec spec;
    private final List<Request> requests;

    private final CloudSimPlus sim;
    private final DatacenterBroker broker;
    private final VmPool pool;

    // Per-request measurements, indexed by position in 'requests'.
    private final double[] dispatchTime;
    private final long[] servedBy;
    private final double[] startTime;
    private final double[] finishTime;

    private final List<TimeSeriesPoint> timeSeries = new ArrayList<>();
    private final List<DecisionRecord> decisions = new ArrayList<>();
    private final List<Double> utilizationHistory = new ArrayList<>();
    private final List<Double> arrivalRateHistory = new ArrayList<>();

    private final Deque<Integer> balancerQueue = new ArrayDeque<>();
    private int nextArrival;          // index of the next request not yet handed to the balancer
    private int arrivedCount;         // requests with arrivalTime <= current time
    private int finishedCount;
    private long tickIndex;
    private double intervalUtilSum;
    private int intervalUtilSamples;
    private int intervalArrivals;
    private double lastScaleUpTime = Double.NEGATIVE_INFINITY;
    private double lastScaleDownTime = Double.NEGATIVE_INFINITY;
    private double endTime = Double.NaN;
    private boolean drainTimedOut;

    private ScalixSimulation(String runId, SimulationConfig cfg, ScalingPolicy policy, WorkloadSpec spec,
                             List<Request> requests) {
        this.runId = runId;
        this.cfg = cfg;
        this.policy = policy;
        this.spec = spec;
        this.requests = requests;
        for (int i = 0; i < requests.size(); i++) {
            if (i > 0 && requests.get(i).arrivalTime() < requests.get(i - 1).arrivalTime()) {
                throw new IllegalArgumentException("Requests must be sorted by arrival time");
            }
        }

        this.dispatchTime = filled(requests.size());
        this.startTime = filled(requests.size());
        this.finishTime = filled(requests.size());
        this.servedBy = new long[requests.size()];
        Arrays.fill(servedBy, -1);

        this.sim = new CloudSimPlus(MIN_TIME_BETWEEN_EVENTS);
        new DatacenterSimple(sim, createHosts(), new VmAllocationPolicySimple());
        this.broker = new DatacenterBrokerSimple(sim);
        // Keep the broker alive through quiet periods; the controller ends the run explicitly.
        broker.setShutdownWhenIdle(false);

        this.pool = new VmPool(new CloudSimProvisioner(), cfg.vmPes(), cfg.vmBootSeconds());
        pool.provisionInitial(policy.limits().initial(), 0);
        new Controller(sim);
    }

    /** Runs the simulation to completion. */
    public static RunResult run(String runId, SimulationConfig cfg, ScalingPolicy policy, WorkloadSpec spec,
                                List<Request> requests) {
        final long t0 = System.nanoTime();
        final ScalixSimulation s = new ScalixSimulation(runId, cfg, policy, spec, requests);
        s.sim.start();
        if (Double.isNaN(s.endTime)) {
            throw new IllegalStateException("Simulation " + runId + " stopped before the controller finished");
        }
        return s.result((System.nanoTime() - t0) / 1_000_000);
    }

    // ------------------------------------------------------------------ infrastructure

    private List<Host> createHosts() {
        final List<Host> hosts = new ArrayList<>();
        for (int h = 0; h < cfg.hostCount(); h++) {
            final List<Pe> pes = new ArrayList<>();
            for (int p = 0; p < cfg.hostPes(); p++) {
                pes.add(new PeSimple(cfg.hostMipsPerPe()));
            }
            hosts.add(new HostSimple(cfg.hostRamMb(), cfg.hostBwMbps(), cfg.hostStorageMb(), pes));
        }
        return hosts;
    }

    /** {@link VmPool.Provisioner} backed by the CloudSim Plus broker. */
    private final class CloudSimProvisioner implements VmPool.Provisioner {
        @Override
        public Vm create(long id) {
            final Vm vm = new VmSimple(id, cfg.vmMipsPerPe(), cfg.vmPes());
            vm.setRam(cfg.vmRamMb()).setBw(cfg.vmBwMbps()).setSize(cfg.vmSizeMb());
            vm.setCloudletScheduler(new CloudletSchedulerSpaceShared());
            broker.submitVm(vm);
            return vm;
        }

        @Override
        public void destroy(Vm vm) {
            vm.shutdown();
        }

        @Override
        public boolean isPlaced(Vm vm) {
            return vm.isCreated() && vm.getHost() != Host.NULL;
        }
    }

    // ------------------------------------------------------------------ controller

    /** CloudSim Plus entity that drives dispatching, sampling and scaling on a fixed tick. */
    private final class Controller extends CloudSimEntity {

        Controller(CloudSimPlus simulation) {
            super(simulation);
            setName("ScalixController");
        }

        @Override
        protected void startInternal() {
            schedule(this, 0, TICK_TAG);
        }

        @Override
        public void processEvent(SimEvent evt) {
            if (evt.getTag() != TICK_TAG) {
                return;
            }
            final double now = tickIndex * cfg.tickSeconds();
            onTick(now);
            // After finish() no further tick is scheduled; the simulation ends once CloudSim Plus has
            // processed the VM destructions requested there.
            if (Double.isNaN(endTime)) {
                tickIndex++;
                schedule(this, (tickIndex * cfg.tickSeconds()) - getSimulation().clock(), TICK_TAG);
            }
        }
    }

    private void onTick(double now) {
        pool.activateReady(now);
        pool.reapDrained(now);

        while (arrivedCount < requests.size() && requests.get(arrivedCount).arrivalTime() <= now) {
            arrivedCount++;
        }

        final double util = sampleActiveUtilization();
        if (!Double.isNaN(util)) {
            intervalUtilSum += util;
            intervalUtilSamples++;
        }

        final long ticksPerDecision = Math.max(1, Math.round(cfg.decisionIntervalSeconds() / cfg.tickSeconds()));
        if (tickIndex > 0 && tickIndex % ticksPerDecision == 0) {
            decide(now);
        }

        final int arrivalsThisTick = dispatch(now);
        intervalArrivals += arrivalsThisTick;

        timeSeries.add(new TimeSeriesPoint(now, pool.provisioned(), pool.count(VmState.ACTIVE),
            pool.count(VmState.BOOTING), pool.count(VmState.DRAINING), util, arrivalsThisTick,
            arrivedCount - finishedCount));

        final boolean arrivalsDone = now + cfg.tickSeconds() >= spec.durationSeconds() && nextArrival >= requests.size();
        if (arrivalsDone && finishedCount == requests.size()) {
            finish(now);
        } else if (now >= spec.durationSeconds() + cfg.maxDrainSeconds()) {
            drainTimedOut = true;
            finish(now);
        }
    }

    private void finish(double now) {
        endTime = now;
        pool.terminateAll(now);
        // Destroy remaining VMs inside the simulation so CloudSim Plus finishes cleanly instead of
        // being cut off with VM destruction events still queued.
        broker.getVmCreatedList().stream().filter(Vm::isCreated).forEach(Vm::shutdown);
    }

    /** Mean instantaneous CPU utilization over ACTIVE VMs, or NaN if none is active. */
    private double sampleActiveUtilization() {
        double sum = 0;
        int n = 0;
        for (ManagedVm m : pool.all()) {
            if (m.state() == VmState.ACTIVE) {
                sum += Math.min(1.0, Math.max(0.0, m.vm().getCpuPercentUtilization()));
                n++;
            }
        }
        return n == 0 ? Double.NaN : sum / n;
    }

    // ------------------------------------------------------------------ dispatching

    /**
     * Hands requests to VMs. Requests still waiting at the balancer go first (delay 0); then every
     * request arriving in [now, now + tick) is submitted with a CloudSim Plus submission delay equal
     * to its exact arrival offset, so response times are not quantised to the tick.
     *
     * @return number of new arrivals in [now, now + tick)
     */
    private int dispatch(double now) {
        while (!balancerQueue.isEmpty()) {
            final var vm = LoadBalancer.select(pool.all());
            if (vm.isEmpty()) {
                break;
            }
            submit(balancerQueue.pollFirst(), vm.get(), now, 0);
        }

        final double windowEnd = now + cfg.tickSeconds();
        int arrivals = 0;
        while (nextArrival < requests.size() && requests.get(nextArrival).arrivalTime() < windowEnd) {
            final int idx = nextArrival++;
            arrivals++;
            final var vm = balancerQueue.isEmpty() ? LoadBalancer.select(pool.all()) : java.util.Optional.<ManagedVm>empty();
            if (vm.isPresent()) {
                final double delay = Math.max(0, requests.get(idx).arrivalTime() - now);
                submit(idx, vm.get(), now, delay);
            } else {
                // No serving VM (or earlier requests still queued): wait at the balancer, FIFO.
                balancerQueue.addLast(idx);
            }
        }
        return arrivals;
    }

    private void submit(int idx, ManagedVm target, double now, double delay) {
        final Request r = requests.get(idx);
        final Cloudlet cl = new CloudletSimple(r.id(), r.lengthMi(), r.pes());
        cl.setSizes(1);
        cl.setVm(target.vm());
        cl.setSubmissionDelay(delay);
        cl.addOnFinishListener(info -> onRequestFinished(idx, target, info.getCloudlet()));
        target.requestDispatched();
        dispatchTime[idx] = now + delay;
        servedBy[idx] = target.id();
        broker.submitCloudlet(cl);
    }

    private void onRequestFinished(int idx, ManagedVm vm, Cloudlet cl) {
        startTime[idx] = cl.getStartTime();
        finishTime[idx] = cl.getFinishTime();
        vm.requestFinished();
        finishedCount++;
    }

    // ------------------------------------------------------------------ scaling

    private void decide(double now) {
        final double rate = intervalArrivals / cfg.decisionIntervalSeconds();
        final double util = intervalUtilSamples == 0 ? Double.NaN : intervalUtilSum / intervalUtilSamples;
        intervalArrivals = 0;
        intervalUtilSum = 0;
        intervalUtilSamples = 0;

        final int before = pool.provisioned();
        if (Double.isNaN(util)) {
            decisions.add(new DecisionRecord(now, ScalingAction.NONE, 0, 0, before, before, Double.NaN, rate,
                Trend.UNKNOWN, 0, false, "engine: no ACTIVE VM was sampled this interval; policy not evaluated"));
            return;
        }
        utilizationHistory.add(util);
        arrivalRateHistory.add(rate);

        final ScalingContext ctx = new ScalingContext(now, before, pool.count(VmState.ACTIVE),
            pool.count(VmState.BOOTING), util, utilizationHistory, arrivalRateHistory, lastScaleUpTime, lastScaleDownTime);
        final ScalingDecision d = policy.decide(ctx);

        // Defence in depth: the engine re-applies the limits even though policies already respect them.
        final VmLimits limits = policy.limits();
        String reason = d.reason();
        int applied = 0;
        if (d.action() == ScalingAction.SCALE_UP) {
            final int allowed = Math.min(d.delta(), limits.max() - before);
            if (allowed < d.delta()) {
                reason += " | engine: clamped to max " + limits.max();
            }
            if (allowed > 0) {
                applied = pool.scaleUp(allowed, now).size();
                lastScaleUpTime = now;
            }
        } else if (d.action() == ScalingAction.SCALE_DOWN) {
            final int allowed = Math.min(-d.delta(), before - limits.min());
            if (allowed < -d.delta()) {
                reason += " | engine: clamped to min " + limits.min();
            }
            if (allowed > 0) {
                applied = -pool.scaleDown(allowed, now).size();
                if (applied != 0) {
                    lastScaleDownTime = now;
                }
            }
        }
        decisions.add(new DecisionRecord(now, d.action(), d.delta(), applied, before, pool.provisioned(), util, rate,
            d.trend(), d.relativeSlope(), d.cooldownBlocked(), reason));
    }

    // ------------------------------------------------------------------ results

    private RunResult result(long wallClockMillis) {
        final List<RequestRecord> recs = new ArrayList<>(requests.size());
        for (int i = 0; i < requests.size(); i++) {
            final Request r = requests.get(i);
            final boolean done = !Double.isNaN(finishTime[i]) && finishTime[i] >= 0;
            recs.add(new RequestRecord(r.id(), r.arrivalTime(), dispatchTime[i], servedBy[i],
                done ? startTime[i] : Double.NaN, done ? finishTime[i] : Double.NaN, r.pes(), r.lengthMi()));
        }
        final List<VmRecord> vmRecs = new ArrayList<>();
        for (ManagedVm m : pool.all()) {
            vmRecs.add(new VmRecord(m.id(), m.pes(), m.requestTime(), m.activatedTime(), m.drainStartTime(),
                m.terminationTime()));
        }
        return new RunResult(runId, spec, policy.name(), policy.parameters(), recs, timeSeries, decisions, vmRecs,
            endTime, drainTimedOut, wallClockMillis);
    }

    private static double[] filled(int n) {
        final double[] a = new double[n];
        Arrays.fill(a, Double.NaN);
        return a;
    }
}
