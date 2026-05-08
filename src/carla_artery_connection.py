"""
Script to integrate recieve artery messages in python for each simulation step
"""
# ==================================================================================================
# -- imports ---------------------------------------------------------------------------------------
# ==================================================================================================

from ast import Try
import numpy as np
import errno, time
import socket
import select
import carla
import traci
import logging
import traceback

from sumo_integration.bridge_helper import BridgeHelper  # pylint: disable=wrong-import-position

HOST = '127.0.0.1'  
PORT = 5555        


class ArterySynchronization(object):

    def __init__(self):
        self.conn = None
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind((HOST, PORT))
        self.s.setblocking(0)
        self.s.listen(5)
        self.on_going_message_recv={"state":False}

    def checkAndConnectclient(self):
        if not self.is_connected():
            try :
                readable, _, _ = select.select([self.s], [], [],0.01)
                if readable:
                    print("connecting artery client")
                    self.conn, _ = self.s.accept() 
                    self.conn.setblocking(0)
            except Exception as e:
                print("Socket check problem")
                logging.error(traceback.format_exc())    
                
    def is_connected(self):
        return not (self.conn is None)
        
    def getCarlaLocation(self,synchronization,cam ):

        x,y = synchronization.net.convertLonLat2XY(cam['receiver_long'],cam['receiver_lat'])
        extent    = carla.Vector3D(cam['Vehicle_Length'] / 200.0, cam['Vehicle_Width'] / 200.0, 0/ 2.0)
        transform = carla.Transform(carla.Location(x, y, 0),
                                carla.Rotation())
        receiver_carla_transform = BridgeHelper.get_carla_transform(transform, extent)
        cam['receiver_pos_x']=receiver_carla_transform.location.x
        cam['receiver_pos_y']=receiver_carla_transform.location.y

        x,y = synchronization.net.convertLonLat2XY(cam['Longitude'],cam['Latitude'])
        extent    = carla.Vector3D(cam['Vehicle_Length'] / 400.0, cam['Vehicle_Width'] / 400.0, 0/ 2.0)
        transform = carla.Transform(carla.Location(x, y, 0),
                                carla.Rotation())
        sender_carla_transform = BridgeHelper.get_carla_transform(transform, extent)
        cam['sender_pos_x']=sender_carla_transform.location.x
        cam['sender_pos_y']=sender_carla_transform.location.y


    def camToDict(self,synchronization,cam):
        full_split=[x.split(':') for x in cam.split('\n')]
        full_cam= {k:v for k,v in full_split[:-1]}

        # Parse receiver information
        full_cam['receiver_artery_id']=int(full_cam['receiver_artery_id'])
        full_cam['receiver_long']= np.double(full_cam['receiver_long'])
        full_cam['receiver_lat']=np.double(full_cam['receiver_lat'])
        full_cam['receiver_speed']= np.double(full_cam['receiver_speed'])

        # Parse sender/station information
        full_cam['Protocol Version']=int(full_cam['Protocol Version'])
        full_cam['Message ID']=int(full_cam['Message ID'])
        full_cam['Station ID']=int(full_cam['Station ID'])
        full_cam['Generation Delta Time']=int(full_cam['Generation Delta Time'])

        # Parse basic container fields if present
        if 'Station Type' in full_cam:
            full_cam['Station Type']=int(full_cam['Station Type'])

        # Parse reference position with proper scaling
        if 'Longitude' in full_cam:
            full_cam['Longitude']=float(full_cam['Longitude'])/10**7
        if 'Latitude' in full_cam:
            full_cam['Latitude']=float(full_cam['Latitude'])/10**7

        # Parse position confidence fields if present
        if 'Semi Major Orientation' in full_cam:
            full_cam['Semi Major Orientation']=int(full_cam['Semi Major Orientation'])
        if 'Semi Major Confidence' in full_cam:
            full_cam['Semi Major Confidence']=int(full_cam['Semi Major Confidence'])
        if 'Semi Minor Confidence' in full_cam:
            full_cam['Semi Minor Confidence']=int(full_cam['Semi Minor Confidence'])

        # Parse altitude if present
        if 'Altitude [Confidence]' in full_cam:
            full_cam['Altitude']=float(full_cam['Altitude [Confidence]'].split('[')[0])
            full_cam['Altitude_Confidence']=float(full_cam['Altitude [Confidence]'].split('[')[1].split(']')[0])
            full_cam.pop('Altitude [Confidence]')

        # Parse heading if present
        if 'Heading [Confidence]' in full_cam:
            full_cam['Heading']=float(full_cam['Heading [Confidence]'].split('[')[0])/10
            full_cam['Heading_Confidence']=float(full_cam['Heading [Confidence]'].split('[')[1].split(']')[0])
            full_cam.pop('Heading [Confidence]')

        # Parse speed if present
        if 'Speed [Confidence]' in full_cam:
            full_cam['Speed']=float(full_cam['Speed [Confidence]'].split('[')[0])/100
            full_cam['Speed_Confidence']=float(full_cam['Speed [Confidence]'].split('[')[1].split(']')[0])
            full_cam.pop('Speed [Confidence]')

        # Parse drive direction if present
        if 'Drive Direction' in full_cam:
            full_cam['Drive Direction']=int(full_cam['Drive Direction'])

        # Parse vehicle dimensions if present
        if 'Vehicle Length [Confidence Indication]' in full_cam:
            full_cam['Vehicle_Length']=float(full_cam['Vehicle Length [Confidence Indication]'].split('[')[0])
            full_cam['Vehicle_Length_Confidence']=float(full_cam['Vehicle Length [Confidence Indication]'].split('[')[1].split(']')[0])
            full_cam.pop('Vehicle Length [Confidence Indication]')

        if 'Vehicle Width' in full_cam:
            full_cam['Vehicle_Width']=float(full_cam['Vehicle Width'])
            full_cam.pop('Vehicle Width')

        # Parse curvature if present
        if 'Curvature [Confidence]' in full_cam:
            full_cam['Curvature']=float(full_cam['Curvature [Confidence]'].split('[')[0])
            full_cam['Curvature_Confidence']=float(full_cam['Curvature [Confidence]'].split('[')[1].split(']')[0])
            full_cam.pop('Curvature [Confidence]')

        if 'Curvature Calculation Mode' in full_cam:
            full_cam['Curvature Calculation Mode']=int(full_cam['Curvature Calculation Mode'])

        # Parse yaw rate if present
        if 'Yaw Rate [Confidence]' in full_cam:
            full_cam['Yaw_Rate']=float(full_cam['Yaw Rate [Confidence]'].split('[')[0])
            full_cam['Yaw_Rate_Confidence']=float(full_cam['Yaw Rate [Confidence]'].split('[')[1].split(']')[0])
            full_cam.pop('Yaw Rate [Confidence]')

        # Parse LDM (Local Dynamic Map) data if present
        if 'ldm_num_vehicles' in full_cam:
            num_ldm_vehicles = int(full_cam['ldm_num_vehicles'])
            full_cam['ldm_vehicles'] = []

            for i in range(num_ldm_vehicles):
                station_key = f'ldm_vehicle_{i}_station_id'
                longitude_key = f'ldm_vehicle_{i}_longitude'
                latitude_key = f'ldm_vehicle_{i}_latitude'
                heading_key = f'ldm_vehicle_{i}_heading'
                speed_key = f'ldm_vehicle_{i}_speed'
                delta_time_key = f'ldm_vehicle_{i}_gen_delta_time'
                required_keys = (
                    station_key, longitude_key, latitude_key,
                    heading_key, speed_key, delta_time_key,
                )
                if not all(key in full_cam for key in required_keys):
                    logging.warning(
                        '[Artery] Skipping incomplete LDM vehicle %s; missing keys: %s',
                        i,
                        [key for key in required_keys if key not in full_cam]
                    )
                    continue

                ldm_vehicle = {
                    'station_id': int(full_cam[station_key]),
                    'longitude': float(full_cam[longitude_key]) / 10**7,
                    'latitude': float(full_cam[latitude_key]) / 10**7,
                    'heading': float(full_cam[heading_key]) / 10,
                    'speed': float(full_cam[speed_key]) / 100,
                    'gen_delta_time': int(full_cam[delta_time_key])
                }
                full_cam['ldm_vehicles'].append(ldm_vehicle)

                # Remove individual LDM keys
                full_cam.pop(station_key, None)
                full_cam.pop(longitude_key, None)
                full_cam.pop(latitude_key, None)
                full_cam.pop(heading_key, None)
                full_cam.pop(speed_key, None)
                full_cam.pop(delta_time_key, None)

        # Remove non-useful container keys if present
        keys_to_remove = ['Low Frequency Container', 'High Frequency Container [Basic Vehicle]',
                         'Reference Position', 'Basic Container', 'ITS PDU Header', 'CoopAwarensess']
        for key in keys_to_remove:
            if key in full_cam:
                full_cam.pop(key)

        # Build Artery to SUMO ID mapping
        if not hasattr(synchronization, 'artery2sumo_ids'):
            synchronization.artery2sumo_ids = {}

        if 'receiver_sumo_id' in full_cam and full_cam['receiver_sumo_id'] is not None:
            full_cam['receiver_sumo_id'] = str(full_cam['receiver_sumo_id'])

        # Map receiver artery ID to SUMO ID
        receiver_artery_id = full_cam.get('receiver_artery_id')
        receiver_sumo_id = full_cam.get('receiver_sumo_id')
        if receiver_artery_id and receiver_sumo_id:
            if receiver_artery_id not in synchronization.artery2sumo_ids:
                synchronization.artery2sumo_ids[receiver_artery_id] = receiver_sumo_id

        # Map sender (Station ID) to SUMO ID if available from LDM
        station_id = full_cam.get('Station ID')
        if station_id and receiver_sumo_id:
            # This is a heuristic - we're mapping based on receiver info
            # More accurate mapping would come from explicit SUMO ID in message
            if station_id not in synchronization.artery2sumo_ids:
                # Station ID might map to a SUMO vehicle, but we need more info
                pass

        # Generate Carla locations for sender and receiver
        self.getCarlaLocation(synchronization, full_cam)

        return full_cam

    def get_ongoing_recv( self ):
        if  self.on_going_message_recv['state'] :
            data = self.conn.recv(self.on_going_message_recv['full_message_size']-len(self.on_going_message_recv['fragment']))
            self.on_going_message_recv['fragment']=self.on_going_message_recv['fragment']+data
            if len(self.on_going_message_recv['fragment'])!= self.on_going_message_recv['full_message_size']:
                self.on_going_message_recv['state']=True
            else:
                self.on_going_message_recv['state']=False
        return self.on_going_message_recv['state']


    def set_ongoing_recv( self,data,size ):
        if len(data)!= size:
            self.on_going_message_recv['state']=True
            self.on_going_message_recv['fragment']=data
            self.on_going_message_recv['full_message_size']=size
        return self.on_going_message_recv['state']


    def recieve_cam_messages(self,synchronization):
        current_step_cams=[]
        while True:
            data = None
            try :
                if self.on_going_message_recv['state']:
                    if  self.get_ongoing_recv():
                        break
                    else:
                        data = self.on_going_message_recv['fragment']
                        if self.on_going_message_recv['full_message_size'] ==6 :
                            next_cam_size = int(data.decode('utf-8'))
                            data = self.conn.recv(next_cam_size)
                            if self.set_ongoing_recv(data,next_cam_size):
                                continue
                else :
                    data = self.conn.recv(6)
                    if len(data)<=0:
                        break
                    if self.set_ongoing_recv(data,6):
                        continue
                    next_cam_size = int(data.decode('utf-8'))
                    data = self.conn.recv(next_cam_size)
                    if self.set_ongoing_recv(data,next_cam_size):
                        continue
            except IOError as e:
                if not data is None :
                    if len(data) == 6:
                        self.set_ongoing_recv(b'', int(data.decode('utf-8')))
                if e.errno != errno.EWOULDBLOCK: 
                    print("e.errno != errno.EWOULDBLOCK",e)
                    logging.error(traceback.format_exc())
                    print(data)
                break
            full_cam = self.camToDict(synchronization,data.decode('utf-8'))

            # DEBUG: Print vehicle ID and message content
            sender_id = full_cam.get('Station ID', 'N/A')
            receiver_artery_id = full_cam.get('receiver_artery_id', 'N/A')
            receiver_sumo_id = full_cam.get('receiver_sumo_id', 'N/A')

            # print(f"\n[DEBUG CAM] Sender Station ID: {sender_id} -> Receiver: Artery={receiver_artery_id}, SUMO={receiver_sumo_id}")

            # Show sender position (GPS and Carla coordinates)
            sender_lon = full_cam.get('Longitude', 0)
            sender_lat = full_cam.get('Latitude', 0)
            sender_carla_x = full_cam.get('sender_pos_x', 'N/A')
            sender_carla_y = full_cam.get('sender_pos_y', 'N/A')
            # print(f"  Sender GPS: ({sender_lon:.6f}, {sender_lat:.6f})")
            # print(f"  Sender Carla: ({sender_carla_x:.2f}, {sender_carla_y:.2f})" if sender_carla_x != 'N/A' else "  Sender Carla: N/A")

            # Show receiver position (GPS and Carla coordinates)
            receiver_lon = full_cam.get('receiver_long', 0)
            receiver_lat = full_cam.get('receiver_lat', 0)
            receiver_carla_x = full_cam.get('receiver_pos_x', 'N/A')
            receiver_carla_y = full_cam.get('receiver_pos_y', 'N/A')
            # print(f"  Receiver GPS: ({receiver_lon:.6f}, {receiver_lat:.6f})")
            # print(f"  Receiver Carla: ({receiver_carla_x:.2f}, {receiver_carla_y:.2f})" if receiver_carla_x != 'N/A' else "  Receiver Carla: N/A")
# 
            # print(f"  Speed: {full_cam.get('Speed', 0):.2f} m/s | Heading: {full_cam.get('Heading', 0):.2f}°")

            # Print LDM data if present
            # if 'ldm_vehicles' in full_cam and full_cam['ldm_vehicles']:
            #     print(f"  LDM Data: {len(full_cam['ldm_vehicles'])} vehicles in local map:")
            #     for ldm_v in full_cam['ldm_vehicles']:
            #         print(f"    - Station {ldm_v['station_id']}: "
            #               f"GPS=({ldm_v['longitude']:.6f}, {ldm_v['latitude']:.6f}), "
            #               f"Speed={ldm_v['speed']:.2f} m/s, Heading={ldm_v['heading']:.2f}°")

            # Print current Artery to SUMO ID mappings
            # if hasattr(synchronization, 'artery2sumo_ids') and synchronization.artery2sumo_ids:
            #     print(f"  [MAPPING] Artery->SUMO IDs: {dict(synchronization.artery2sumo_ids)}")

            current_step_cams.append(full_cam)
        return current_step_cams

    def shutdownAndClose(self):
        try :
            self.s.shutdown(socket.SHUT_RDWR)
            self.s.close()
        except OSError as e :
            print("trying to close already closed socket")
            
