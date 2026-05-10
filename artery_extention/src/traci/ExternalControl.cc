#include "traci/ExternalControl.h"
#include "traci/Core.h"
#include "traci/API.h"
#include <inet/common/ModuleAccess.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <cstring>
#include <vector>
#include <chrono>
#include <queue>
#include <limits>

// Generated protobuf headers (will be in build directory)
// The actual include path will be set by CMake based on build directory
#include "ControlService.pb.h"

using namespace omnetpp;
using namespace artery::traci;

Define_Module(ExternalControl)





void ExternalControl::initialize()
{
    m_port = par("port");
    m_core = inet::getModuleFromPar<traci::Core>(par("coreModule"), this);
    
    // Disable auto-stepping immediately when ExternalControl is enabled
    EV_INFO << "Auto-stepping disabled" << endl;
    m_core->setAutoStepping(false);
    
    // Process the connect event at t=0 before starting external control
    if (simTime() == SimTime::ZERO && !m_core->isConnected()) {
        EV_INFO << "Triggering TraCI connection..." << endl;
        
        // Get the connect event from the FES
        cFutureEventSet* fes = getSimulation()->getFES();
        cEvent* connectEvent = nullptr;
        
        // Find the connect event scheduled at t=0 for the core module
        for (int i = 0; i < fes->getLength(); i++) {
            cEvent* event = fes->get(i);
            if (event->getArrivalTime() == SimTime::ZERO && 
                event->getTargetObject() == m_core) {
                connectEvent = event;
                break;
            }
        }
        
        if (connectEvent) {
            EV_INFO << "Found connect event, processing it..." << endl;
            fes->remove(connectEvent);
            m_core->handleMessage(check_and_cast<cMessage*>(connectEvent));
            
            if (m_core->isConnected()) {
                EV_INFO << "TraCI connected successfully" << endl;
            }
        } else {
            EV_WARN << "No connect event found at t=0" << endl;
        }
    }
    
    EV_INFO << "External control enabled - simulation will wait for step commands" << endl;
    
    m_running = true;
    m_serverSocket = -1;  // Initialize server socket
    m_processEvent = new cMessage("processSteps");
    m_processEvent->setSchedulingPriority(std::numeric_limits<short>::min());
    SimTime start = m_core->par("startTime");
    SimTime firstActivation = std::max(simTime(), start);
    scheduleAt(firstActivation, m_processEvent);
    m_thread = std::thread(&ExternalControl::serverThread, this);
    EV_INFO << "External control server (Protobuf) listening on port " << m_port << endl;
}

ExternalControl::~ExternalControl()
{
    // Ensure cleanup happens even if finish() isn't called (e.g., on Ctrl+C)
    m_running = false;
    m_queueCond.notify_all();
    
    if (m_serverSocket >= 0) {
        ::shutdown(m_serverSocket, SHUT_RDWR);
        ::close(m_serverSocket);
        m_serverSocket = -1;
    }
    
    if (m_thread.joinable()) {
        m_thread.join();
    }
}

void ExternalControl::finish()
{
    m_running = false;
    m_queueCond.notify_all();
    
    // Close the server socket to unblock accept() and allow thread to exit
    if (m_serverSocket >= 0) {
        ::shutdown(m_serverSocket, SHUT_RDWR);
        ::close(m_serverSocket);
        m_serverSocket = -1;
    }
    
    if (m_thread.joinable()) {
        m_thread.join();
    }
    cancelAndDelete(m_processEvent);
    m_processEvent = nullptr;
    
    // Clean up any pending step requests
    std::lock_guard<std::mutex> lock(m_queueMutex);
    while (!m_stepQueue.empty()) {
        NextStepRequest* req = m_stepQueue.front();
        m_stepQueue.pop();
        delete req;
    }
}



void ExternalControl::handleMessage(cMessage* msg)
{
    if (msg == m_processEvent) {
        processPendingSteps();
    } else {
        delete msg;
    }
}

void ExternalControl::processPendingSteps()
{
    while (m_running) {
        NextStepRequest* req = nullptr;
        {
            std::unique_lock<std::mutex> lock(m_queueMutex);
            m_queueCond.wait(lock, [this] {
                return !m_running || !m_stepQueue.empty();
            });
            if (!m_running) {
                return;
            }
            req = m_stepQueue.front();
            m_stepQueue.pop();
        }

        EV_INFO << "ExternalControl: processing step request of "
                << req->stepCount << " steps at simTime=" << simTime() << endl;

        try {
            // Execute steps on main simulation thread
            for (uint32_t i = 0; i < req->stepCount; ++i) {
                m_core->stepSimulation();
            }
            
            // IMPORTANT: Advance OMNeT++ simulation time
            SimTime stepInterval = m_core->getStepInterval();
            SimTime totalAdvance = stepInterval * req->stepCount;
            SimTime nextTime = simTime() + totalAdvance;
            
            // Reschedule event to advance time
            if (m_processEvent->isScheduled()) {
                cancelEvent(m_processEvent);
            }
            scheduleAt(nextTime, m_processEvent);
            
            // Create response
            ControlResponse response;
            auto* step_resp = response.mutable_step();
            step_resp->set_success(true);
            step_resp->set_current_time(nextTime.dbl());  // Future time after steps
            
            // Serialize response
            response.SerializeToString(&req->responseData);
            
        } catch (const std::exception& e) {
            // Error handling
            ControlResponse response;
            auto* step_resp = response.mutable_step();
            step_resp->set_success(false);
            step_resp->set_error(e.what());
            response.SerializeToString(&req->responseData);
        }
        
        // Notify waiting thread
        {
            std::lock_guard<std::mutex> reqLock(req->responseMutex);
            req->responseReady = true;
        }
        req->responseCond.notify_one();
        
        // Delete the request after processing
        delete req;
    }
}


bool ExternalControl::readVarint(int socket, uint32_t& value)
{
    value = 0;
    int shift = 0;
    uint8_t byte;
    
    while (shift < 32) {
        ssize_t n = recv(socket, &byte, 1, 0);
        if (n <= 0) return false;
        
        value |= (byte & 0x7F) << shift;
        if ((byte & 0x80) == 0) {
            return true;
        }
        shift += 7;
    }
    return false;
}

bool ExternalControl::sendVarint(int socket, uint32_t value)
{
    std::vector<uint8_t> bytes;
    while (value >= 0x80) {
        bytes.push_back((value & 0x7F) | 0x80);
        value >>= 7;
    }
    bytes.push_back(value & 0x7F);
    
    return ::send(socket, bytes.data(), bytes.size(), 0) == static_cast<ssize_t>(bytes.size());
}

void ExternalControl::processClient(int client)
{
    std::string response_data;
    ControlResponse response;

    try {
        //
        // --- 1. Read message length ---
        //
        uint32_t msg_len = 0;
        if (!readVarint(client, msg_len) || msg_len == 0 || msg_len > 1024 * 1024) {
            EV_WARN << "Invalid message length from client" << endl;

            auto* r = response.mutable_step();
            r->set_success(false);
            r->set_error("Invalid message length");
            goto SEND_NOW;
        }

        //
        // --- 2. Read full message ---
        //
        std::vector<char> buffer(msg_len);
        ssize_t total_read = 0;

        while (total_read < (ssize_t)msg_len) {
            ssize_t n = recv(client, buffer.data() + total_read, msg_len - total_read, 0);
            if (n <= 0) {
                EV_WARN << "Failed to read complete request" << endl;

                auto* r = response.mutable_step();
                r->set_success(false);
                r->set_error("Connection closed while reading request");
                goto SEND_NOW;
            }
            total_read += n;
        }

        //
        // --- 3. Parse protobuf request ---
        //
        ControlRequest request;
        if (!request.ParseFromArray(buffer.data(), msg_len)) {
            EV_WARN << "Failed to parse protobuf request" << endl;

            auto* r = response.mutable_step();
            r->set_success(false);
            r->set_error("Failed to parse request");
            goto SEND_NOW;
        }

        //
        // --- 4. Process request types ---
        //
        if (request.has_step()) {
            const auto& step_req = request.step();
            uint32_t count = step_req.count() > 0 ? step_req.count() : 1;

            // Ensure TraCI is connected
            if (!m_core->isConnected()) {
                auto* r = response.mutable_step();
                r->set_success(false);
                r->set_error("TraCI not connected yet");
                goto SEND_NOW;
            }

            // First step → disable autostepping
            if (m_core->isAutoStepping()) {
                m_core->setAutoStepping(false);
            }

            //
            // --- QUEUE A STEP REQUEST ---
            //
            NextStepRequest* req = new NextStepRequest();
            req->stepCount = count;

            {
                std::lock_guard<std::mutex> lock(m_queueMutex);
                m_stepQueue.push(req);
                m_queueCond.notify_one();
            }

            //
            // --- WAIT for main thread to process it ---
            //
            std::unique_lock<std::mutex> lk(req->responseMutex);
            bool done = req->responseCond.wait_for(
                lk, std::chrono::seconds(5),
                [req] { return req->responseReady; }
            );

            if (!done) {
                delete req;
                auto* r = response.mutable_step();
                r->set_success(false);
                r->set_error("Step request timed out");
                goto SEND_NOW;
            }

            // response_data already filled by main thread
            response_data = req->responseData;
            lk.unlock(); // unlock before deletion by main thread processing
            goto SEND_NOW;
        }

        //
        // GET TIME
        //
        if (request.has_get_time()) {
            auto* t = response.mutable_get_time();
            t->set_time(m_core->getCurrentTime().dbl());
            goto SEND_NOW;
        }

        //
        // GET STATUS
        //
        if (request.has_get_status()) {
            auto api = m_core->getAPI();
            auto vehicles = api->vehicle.getIDList();

            auto* s = response.mutable_get_status();
            s->set_connected(m_core->isConnected());
            s->set_time(m_core->getCurrentTime().dbl());
            s->set_step_interval(m_core->getStepInterval().dbl());
            s->set_vehicle_count(vehicles.size());
            goto SEND_NOW;
        }

        //
        // GET VEHICLES
        //
        if (request.has_get_vehicles()) {
            auto api = m_core->getAPI();
            auto vehicles = api->vehicle.getIDList();
            auto* v = response.mutable_get_vehicles();
            for (const auto& x : vehicles)
                v->add_vehicle_ids(x);
            goto SEND_NOW;
        }

        //
        // GET VEHICLE INFO
        //
        if (request.has_get_vehicle_info()) {
            const auto& info_req = request.get_vehicle_info();
            auto api = m_core->getAPI();

            try {
                auto pos = api->vehicle.getPosition(info_req.vehicle_id());
                auto speed = api->vehicle.getSpeed(info_req.vehicle_id());
                auto angle = api->vehicle.getAngle(info_req.vehicle_id());
                auto road = api->vehicle.getRoadID(info_req.vehicle_id());
                auto lane = api->vehicle.getLaneIndex(info_req.vehicle_id());

                auto* v = response.mutable_get_vehicle_info();
                v->set_vehicle_id(info_req.vehicle_id());
                v->set_position_x(pos.x);
                v->set_position_y(pos.y);
                v->set_speed(speed);
                v->set_angle(angle);
                v->set_road_id(road);
                v->set_lane_index(lane);

            } catch (const std::exception& e) {
                auto* r = response.mutable_step();
                r->set_success(false);
                r->set_error(std::string("Vehicle not found: ") + e.what());
            }
            goto SEND_NOW;
        }

    } catch (const std::exception& e) {
        auto* r = response.mutable_step();
        r->set_success(false);
        r->set_error(e.what());
    }

SEND_NOW:

    //
    // If step handler produced serialized bytes, use them.
    //
    if (response_data.empty()) {
        if (!response.SerializeToString(&response_data)) {
            ControlResponse fallback;
            auto* r = fallback.mutable_step();
            r->set_success(false);
            r->set_error("Failed to serialize response");
            fallback.SerializeToString(&response_data);
        }
    }

    sendVarint(client, response_data.size());
    ::send(client, response_data.data(), response_data.size(), 0);

    close(client);
}


// void ExternalControl::processClient(int client)
// {
//     std::string response_data;
    
//     try {
//         // Read message length (varint)
//         uint32_t msg_len;
//         if (!readVarint(client, msg_len) || msg_len == 0 || msg_len > 1024 * 1024) {
//             EV_WARN << "Invalid message length from client" << endl;
//             ControlResponse error_resp;
//             auto* step_resp = error_resp.mutable_step();
//             step_resp->set_success(false);
//             step_resp->set_error("Invalid message length");
//             error_resp.SerializeToString(&response_data);
//             goto send_response;
//         }
        
//         // Read message data
//         std::vector<char> buffer(msg_len);
//         ssize_t total_read = 0;
//         while (total_read < static_cast<ssize_t>(msg_len)) {
//             ssize_t n = recv(client, buffer.data() + total_read, msg_len - total_read, 0);
//             if (n <= 0) {
//                 EV_WARN << "Failed to read complete message from client" << endl;
//                 ControlResponse error_resp;
//                 auto* step_resp = error_resp.mutable_step();
//                 step_resp->set_success(false);
//                 step_resp->set_error("Connection closed while reading message");
//                 error_resp.SerializeToString(&response_data);
//                 goto send_response;
//             }
//             total_read += n;
//         }
        
//         // Parse request
//         ControlRequest request;
//         if (!request.ParseFromArray(buffer.data(), msg_len)) {
//             EV_WARN << "Failed to parse request from client" << endl;
//             ControlResponse error_resp;
//             auto* step_resp = error_resp.mutable_step();
//             step_resp->set_success(false);
//             step_resp->set_error("Failed to parse request");
//             error_resp.SerializeToString(&response_data);
//             goto send_response;
//         }
        
//         // Process request and create response
//         ControlResponse response;
        
//         try {
//         if (request.has_step()) {
//             const auto& step_req = request.step();
//             uint32_t count = step_req.count() > 0 ? step_req.count() : 1;
            
//             // Disable auto-stepping on first step request
//             if (m_core->isAutoStepping()) {
//                 m_core->setAutoStepping(false);
//             }
            
//             // Check if TraCI is connected
//             if (!m_core->isConnected()) {
//                 ControlResponse not_ready_resp;
//                 auto* step_resp = not_ready_resp.mutable_step();
//                 step_resp->set_success(false);
//                 step_resp->set_error("TraCI not connected yet");
//                 not_ready_resp.SerializeToString(&response_data);
//             } else {
//                 // Create step request and add to queue
//                 NextStepRequest* req = new NextStepRequest();
//                 req->stepCount = count;
                
//                 // Add to queue (will be processed by main thread)
//                 {
//                     std::lock_guard<std::mutex> lock(m_queueMutex);
//                     m_stepQueue.push(req);
//                 }
                
//                 // Wait for step to complete (with timeout)
//                 std::unique_lock<std::mutex> reqLock(req->responseMutex);
//                 bool completed = req->responseCond.wait_for(reqLock, std::chrono::seconds(5), 
//                                                             [req] { return req->responseReady; });
//                 if (completed) {
//                     // Step completed, get response
//                     response_data = req->responseData;
//                     reqLock.unlock();  // Release lock before request might be deleted
//                     // Request will be deleted by processPendingSteps
//                 } else {
//                     // Timeout - clean up and send error
//                     reqLock.unlock();
//                     // Try to remove from queue (best effort)
//                     {
//                         std::lock_guard<std::mutex> queueLock(m_queueMutex);
//                         std::queue<NextStepRequest*> tempQueue;
//                         while (!m_stepQueue.empty()) {
//                             NextStepRequest* qreq = m_stepQueue.front();
//                             m_stepQueue.pop();
//                             if (qreq != req) {
//                                 tempQueue.push(qreq);
//                             }
//                         }
//                         m_stepQueue = tempQueue;
//                     }
//                     delete req;  // Clean up timed-out request
                    
//                     ControlResponse timeout_resp;
//                     auto* step_resp = timeout_resp.mutable_step();
//                     step_resp->set_success(false);
//                     step_resp->set_error("Step request timeout - simulation may not be advancing");
//                     timeout_resp.SerializeToString(&response_data);
//                 }
//             }
            
//             // Send response if we have one
//             if (!response_data.empty()) {
//                 if (sendVarint(client, response_data.length())) {
//                     ::send(client, response_data.data(), response_data.length(), 0);
//                 }
//             }
//             close(client);
//             return;
//         }
//         else if (request.has_get_time()) {
//             auto* time_resp = response.mutable_get_time();
//             time_resp->set_time(m_core->getCurrentTime().dbl());
//         }
//         else if (request.has_get_status()) {
//             // These are safe to call from any thread (read-only operations)
//             auto api = m_core->getAPI();
//             auto vehicles = api->vehicle.getIDList();
//             auto* status_resp = response.mutable_get_status();
//             status_resp->set_connected(m_core->isConnected());
//             status_resp->set_time(m_core->getCurrentTime().dbl());
//             status_resp->set_step_interval(m_core->getStepInterval().dbl());
//             status_resp->set_vehicle_count(vehicles.size());
//         }
//         else if (request.has_get_vehicles()) {
//             // Safe to call from any thread (read-only)
//             auto api = m_core->getAPI();
//             auto vehicles = api->vehicle.getIDList();
//             auto* vehicles_resp = response.mutable_get_vehicles();
//             for (const auto& v : vehicles) {
//                 vehicles_resp->add_vehicle_ids(v);
//             }
//         }
//         else if (request.has_get_vehicle_info()) {
//             // Safe to call from any thread (read-only)
//             const auto& info_req = request.get_vehicle_info();
//             auto api = m_core->getAPI();
            
//             try {
//                 auto pos = api->vehicle.getPosition(info_req.vehicle_id());
//                 auto speed = api->vehicle.getSpeed(info_req.vehicle_id());
//                 auto angle = api->vehicle.getAngle(info_req.vehicle_id());
//                 auto road = api->vehicle.getRoadID(info_req.vehicle_id());
//                 auto lane = api->vehicle.getLaneIndex(info_req.vehicle_id());
                
//                 auto* info_resp = response.mutable_get_vehicle_info();
//                 info_resp->set_vehicle_id(info_req.vehicle_id());
//                 info_resp->set_position_x(pos.x);
//                 info_resp->set_position_y(pos.y);
//                 info_resp->set_speed(speed);
//                 info_resp->set_angle(angle);
//                 info_resp->set_road_id(road);
//                 info_resp->set_lane_index(lane);
//             } catch (const std::exception& e) {
//                 // Vehicle not found or error
//                 auto* step_resp = response.mutable_step();
//                 step_resp->set_success(false);
//                 step_resp->set_error(std::string("Vehicle not found: ") + e.what());
//             }
//         }
//     } catch (const std::exception& e) {
//         // Error handling - create error response
//         ControlResponse error_resp;
//         auto* step_resp = error_resp.mutable_step();
//         step_resp->set_success(false);
//         step_resp->set_error(e.what());
//         error_resp.SerializeToString(&response_data);
//     }
// }
    
//     // Serialize and send response (for non-step requests)
//     if (response_data.empty()) {
//         if (!response.SerializeToString(&response_data)) {
//             // Fallback error response if serialization fails
//             ControlResponse error_resp;
//             auto* step_resp = error_resp.mutable_step();
//             step_resp->set_success(false);
//             step_resp->set_error("Failed to serialize response");
//             error_resp.SerializeToString(&response_data);
//         }
//     }
    
//     // Send response if we have one
//     if (!response_data.empty()) {
//         if (sendVarint(client, response_data.length())) {
//             ssize_t sent = ::send(client, response_data.data(), response_data.length(), 0);
//             if (sent < 0) {
//                 EV_WARN << "Failed to send response to client" << endl;
//             }
//         } else {
//             EV_WARN << "Failed to send response length to client" << endl;
//         }
//     } else {
//         EV_WARN << "No response data to send to client" << endl;
//     }
    
//     close(client);
// }

void ExternalControl::serverThread()
{
    m_serverSocket = socket(AF_INET, SOCK_STREAM, 0);
    if (m_serverSocket < 0) {
        EV_ERROR << "Failed to create socket" << endl;
        return;
    }
    
    int opt = 1;
    setsockopt(m_serverSocket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(m_port);
    
    if (bind(m_serverSocket, (sockaddr*)&addr, sizeof(addr)) < 0) {
        EV_ERROR << "Failed to bind control server to port " << m_port << endl;
        close(m_serverSocket);
        m_serverSocket = -1;
        return;
    }
    
    if (listen(m_serverSocket, 5) < 0) {
        EV_ERROR << "Failed to listen on port " << m_port << endl;
        close(m_serverSocket);
        m_serverSocket = -1;
        return;
    }
    
    while (m_running) {
        int client = accept(m_serverSocket, nullptr, nullptr);
        if (client < 0) {
            if (m_running) {
                EV_WARN << "Accept failed" << endl;
            }
            // Socket was closed, exit the loop
            break;
        }
        
        processClient(client);
    }
    
    // Close socket if not already closed
    if (m_serverSocket >= 0) {
        close(m_serverSocket);
        m_serverSocket = -1;
    }
}
