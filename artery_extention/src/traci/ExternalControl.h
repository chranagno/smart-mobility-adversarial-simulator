#ifndef EXTERNALCONTROL_H
#define EXTERNALCONTROL_H

#include <omnetpp/csimplemodule.h>
#include <omnetpp/cmessage.h>
#include <omnetpp/simtime.h>
#include <thread>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <string>

namespace traci { class Core; }

struct NextStepRequest {
    uint32_t stepCount;
    std::string responseData;
    std::mutex responseMutex;
    std::condition_variable responseCond;
    bool responseReady = false;
};

class ExternalControl : public omnetpp::cSimpleModule
{
public:
    ~ExternalControl();
    void initialize() override;
    void finish() override;
    void handleMessage(omnetpp::cMessage* msg) override;

private:
    void serverThread();
    bool readVarint(int socket, uint32_t& value);
    bool sendVarint(int socket, uint32_t value);
    void processClient(int client);
    void processPendingSteps();
    
    traci::Core* m_core;
    std::thread m_thread;
    std::atomic<bool> m_running;
    int m_port;
    int m_serverSocket;
    
    // Queue for step requests from server thread
    std::mutex m_queueMutex;
    std::queue<NextStepRequest*> m_stepQueue;
    std::condition_variable m_queueCond;
    omnetpp::cMessage* m_processEvent;

};

#endif /* EXTERNALCONTROL_H */
