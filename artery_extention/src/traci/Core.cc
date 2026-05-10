#include "traci/Core.h"
#include "traci/Launcher.h"
#include "traci/API.h"
#include "traci/SubscriptionManager.h"
#include <inet/common/ModuleAccess.h>
#include <limits>
#include <exception>

Define_Module(traci::Core)

using namespace omnetpp;
using std::endl;

namespace
{
const simsignal_t initSignal = cComponent::registerSignal("traci.init");
const simsignal_t stepSignal = cComponent::registerSignal("traci.step");
const simsignal_t closeSignal = cComponent::registerSignal("traci.close");
}

namespace traci
{

Core::Core() : m_traci(new API()), m_subscriptions(nullptr), m_autoStepping(true)
{
}

Core::~Core()
{
    cancelAndDelete(m_connectEvent);
    cancelAndDelete(m_updateEvent);
}

void Core::initialize()
{
    m_connectEvent = new cMessage("connect TraCI");
    m_updateEvent = new cMessage("TraCI step");
    m_updateEvent->setSchedulingPriority(std::numeric_limits<short>::min());
    cModule* manager = getParentModule();
    m_launcher = inet::getModuleFromPar<Launcher>(par("launcherModule"), manager);
    m_stopping = par("selfStopping");
    SimTime startTime = par("startTime");
    
    std::cout << "[TraCI::Core] Scheduling connect event at " << startTime.dbl() << "s" << std::endl;
    EV_INFO << "TraCI core scheduling connect event at " << startTime << endl;
    scheduleAt(startTime, m_connectEvent);
    m_subscriptions = inet::getModuleFromPar<SubscriptionManager>(par("subscriptionsModule"), manager, false);
}

void Core::finish()
{
    emit(closeSignal, simTime());
    if (!m_connectEvent->isScheduled()) {
        m_traci->close();
    }
}

void Core::handleMessage(cMessage* msg)
{
    if (msg == m_updateEvent) {
        EV_DEBUG << "TraCI step event at simTime=" << simTime()
                 << ", delta=" << m_updateInterval << endl;
        try {
            m_traci->simulationStep();
            EV_TRACE << "TraCI simulationStep completed at simTime=" << simTime() << endl;
            if (m_subscriptions) {
                m_subscriptions->step();
            }
            emit(stepSignal, simTime());
        } catch (const std::exception& e) {
            EV_ERROR << "TraCI simulationStep failed: " << e.what() << endl;
            throw;
        }

        int expectedVehicles = m_traci->simulation.getMinExpectedNumber();
        bool shouldContinue = !m_stopping || expectedVehicles > 0;
        
        if (m_autoStepping && shouldContinue) {
            SimTime next = simTime() + m_updateInterval;
            EV_DEBUG << "Scheduling next TraCI step at " << next << endl;
            scheduleAt(next, m_updateEvent);
        } else if (!m_autoStepping) {
            EV_INFO << "Auto-stepping disabled, waiting for external control" << endl;
        } else {
            EV_WARN << "TraCI core stops scheduling steps because stopping="
                    << m_stopping << " and no expected vehicles remain" << endl;
        }
    } else if (msg == m_connectEvent) {
        std::cout << "[TraCI::Core] CONNECT EVENT received at simTime=" << simTime().dbl() << "s" << std::endl;
        std::cout << "[TraCI::Core] Attempting to connect to TraCI server..." << std::endl;
        
        try {
            ServerEndpoint endpoint = m_launcher->launch();
            std::cout << "[TraCI::Core] Launcher returned endpoint: " << endpoint.hostname << ":" << endpoint.port << std::endl;
            
            m_traci->connect(endpoint);
            std::cout << "[TraCI::Core] Successfully connected to TraCI!" << std::endl;
            
            checkVersion();
            syncTime();
            emit(initSignal, simTime());
            m_updateInterval = Time { m_traci->simulation.getDeltaT() };
            SimTime firstStep = simTime() + m_updateInterval;
            
            std::cout << "[TraCI::Core] Connected! delta=" << m_updateInterval.dbl() 
                      << "s, first step at " << firstStep.dbl() << "s" << std::endl;
            EV_INFO << "TraCI connected, delta=" << m_updateInterval
                    << ", scheduling first step at " << firstStep << endl;
            scheduleAt(firstStep, m_updateEvent);
        } catch (const std::exception& e) {
            std::cerr << "[TraCI::Core] ERROR during connection: " << e.what() << std::endl;
            EV_ERROR << "TraCI connection failed: " << e.what() << endl;
            throw;
        }
    }
}

void Core::checkVersion()
{
    int expected = par("version");
    if (expected == 0) {
        expected = libsumo::TRACI_VERSION;
        EV_INFO << "Defaulting expected TraCI API level to client API version " << expected << endl;
    }

    const auto actual = m_traci->getVersion();
    EV_INFO << "TraCI server is " << actual.second << " with API level " << actual.first << endl;

    if (actual.first < 18) {
        EV_FATAL << "Reported TraCI server version is incompatible with client API" << endl;
        throw cRuntimeError("Version of TraCI server is too old (required: 18, provided: %i), please update SUMO!", actual.first);
    } else if (expected < 0) {
        EV_DEBUG << "No specific TraCI server version requested, accepting connection..." << endl;
    } else if (expected != actual.first) {
        EV_FATAL << "Reported TraCI server version does not match expected version " << expected << endl;
        throw cRuntimeError("TraCI server version mismatch (expected: %i, actual: %i)", expected, actual.first);
    }
}

void Core::syncTime()
{
    SimTime offset { m_traci->simulation.getCurrentTime(), SIMTIME_MS };
    const SimTime now = simTime();
    if (!now.isZero()) {
        m_traci->simulationStep((now + offset).dbl());
    }
}

std::shared_ptr<API> Core::getAPI()
{
    return m_traci;
}

void Core::stepSimulation()
{
    if (!m_traci) {
        throw cRuntimeError("TraCI not connected");
    }
    try {
        m_traci->simulationStep();
        EV_TRACE << "TraCI simulationStep completed at simTime=" << simTime() << endl;
        if (m_subscriptions) {
            m_subscriptions->step();
        }
        emit(stepSignal, simTime());
    } catch (const std::exception& e) {
        EV_ERROR << "TraCI simulationStep failed: " << e.what() << endl;
        throw;
    }
}

void Core::setAutoStepping(bool enable)
{
    m_autoStepping = enable;
    EV_INFO << "Auto-stepping " << (enable ? "enabled" : "disabled") << endl;
}

bool Core::isConnected() const
{
    return m_traci != nullptr && m_connectEvent != nullptr && !m_connectEvent->isScheduled();
}

} // namespace traci
