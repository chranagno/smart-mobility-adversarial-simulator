#!/usr/bin/env python3
"""
Artery External Control API - Python Client

This client provides a Python interface to control Artery simulations
via the external control API using Protocol Buffers.

Requirements:
    pip install protobuf

Usage:
    python artery_control_client.py
"""

import os
import sys
sys.path.append("/home/chranagno/Workspace/repos/simulator_paper/src/python_artery_client")
# Workaround for protobuf version compatibility
# Must be set BEFORE importing any protobuf modules
# If protoc version < 3.19.0 but protobuf Python >= 4.21, use pure Python implementation
# This is slower but works with older protoc versions
if 'PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION' not in os.environ:
    # Set workaround by default - will use pure Python if needed
    # This allows the client to work even with older protoc versions
    os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'

import socket
from typing import Optional, List, Dict

# Import generated protobuf classes
# These will be generated from ControlService.proto
# For now, we'll show the structure - you'll need to compile the proto file first
try:
    import ControlService_pb2 as pb
except ImportError:
    print("Error: ControlService_pb2 not found.")
    print("Please compile ControlService.proto first:")
    print("  ./setup_protobuf.sh")
    print("  or")
    print("  protoc --python_out=. --proto_path=../src/traci ../src/traci/ControlService.proto")
    sys.exit(1)


class ArteryControlClient:
    """Client for controlling Artery simulations via external control API."""
    
    def __init__(self, host: str = 'localhost', port: int = 8888):
        """
        Initialize the client.
        
        Args:
            host: Server hostname (default: localhost)
            port: Server port (default: 8888)
        """
        self.host = host
        self.port = port
    
    def _send_request(self, request: pb.ControlRequest) -> pb.ControlResponse:
        """
        Send a request and receive response.
        
        Args:
            request: ControlRequest protobuf message
            
        Returns:
            ControlResponse protobuf message
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)  # 5 second timeout
        try:
            try:
                sock.connect((self.host, self.port))
            except ConnectionRefusedError:
                raise IOError(f"Connection refused. Is the simulation running with ExternalControl enabled?")
            except socket.timeout:
                raise IOError(f"Connection timeout. Is the simulation running?")
            
            # Serialize request
            request_data = request.SerializeToString()
            
            # Send message length (varint)
            self._send_varint(sock, len(request_data))
            
            # Send message
            sock.sendall(request_data)
            
            # Read response length (varint)
            resp_len = self._read_varint(sock)
            
            # Read response
            response_data = sock.recv(resp_len)
            if len(response_data) < resp_len:
                # Try to read remaining data
                remaining = resp_len - len(response_data)
                while remaining > 0:
                    chunk = sock.recv(remaining)
                    if not chunk:
                        raise IOError("Connection closed while reading response")
                    response_data += chunk
                    remaining -= len(chunk)
            
            # Parse response
            response = pb.ControlResponse()
            response.ParseFromString(response_data)
            
            return response
            
        except socket.timeout:
            raise IOError("Connection timeout while communicating with server")
        except ConnectionResetError:
            raise IOError("Connection reset by server")
        finally:
            sock.close()
    
    def _read_varint(self, sock: socket.socket) -> int:
        """Read a varint-encoded integer from socket."""
        result = 0
        shift = 0
        while shift < 32:
            byte = sock.recv(1)
            if not byte:
                raise IOError("Connection closed unexpectedly while reading varint")
            b = byte[0]
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                return result
            shift += 7
        raise ValueError("Varint too large")
    
    def _send_varint(self, sock: socket.socket, value: int):
        """Send a varint-encoded integer to socket."""
        bytes_list = []
        while value >= 0x80:
            bytes_list.append((value & 0x7F) | 0x80)
            value >>= 7
        bytes_list.append(value & 0x7F)
        sock.sendall(bytes(bytes_list))
    
    def step(self, count: int = 1) -> Dict:
        """
        Step the simulation.
        
        Args:
            count: Number of steps to execute (default: 1)
            
        Returns:
            Dictionary with 'success', 'current_time', and optionally 'error'
        """
        request = pb.ControlRequest()
        request.step.count = count
        
        response = self._send_request(request)
        
        if response.HasField('step'):
            step_resp = response.step
            return {
                'success': step_resp.success,
                'current_time': step_resp.current_time,
                'error': step_resp.error if step_resp.error else None
            }
        else:
            return {'success': False, 'error': 'Unexpected response type'}
    
    def get_time(self) -> float:
        """
        Get current simulation time.
        
        Returns:
            Current simulation time in seconds
        """
        request = pb.ControlRequest()
        request.get_time.SetInParent()
        
        response = self._send_request(request)
        
        if response.HasField('get_time'):
            return response.get_time.time
        else:
            raise ValueError("Unexpected response type")
    
    def get_status(self) -> Dict:
        """
        Get simulation status.
        
        Returns:
            Dictionary with 'connected', 'time', 'step_interval', 'vehicle_count'
        """
        request = pb.ControlRequest()
        request.get_status.SetInParent()
        
        response = self._send_request(request)
        
        if response.HasField('get_status'):
            status = response.get_status
            return {
                'connected': status.connected,
                'time': status.time,
                'step_interval': status.step_interval,
                'vehicle_count': status.vehicle_count
            }
        else:
            raise ValueError("Unexpected response type")
    
    def get_vehicles(self) -> List[str]:
        """
        Get list of all vehicle IDs.
        
        Returns:
            List of vehicle ID strings
        """
        request = pb.ControlRequest()
        request.get_vehicles.SetInParent()
        
        response = self._send_request(request)
        
        if response.HasField('get_vehicles'):
            return list(response.get_vehicles.vehicle_ids)
        else:
            raise ValueError("Unexpected response type")
    
    def get_vehicle_info(self, vehicle_id: str) -> Dict:
        """
        Get detailed information about a vehicle.
        
        Args:
            vehicle_id: ID of the vehicle
            
        Returns:
            Dictionary with vehicle information:
            - vehicle_id: Vehicle ID
            - position_x: X position in meters
            - position_y: Y position in meters
            - speed: Speed in m/s
            - angle: Heading angle in degrees
            - road_id: Current road ID
            - lane_index: Current lane index
        """
        request = pb.ControlRequest()
        request.get_vehicle_info.vehicle_id = vehicle_id
        
        response = self._send_request(request)
        
        if response.HasField('get_vehicle_info'):
            info = response.get_vehicle_info
            return {
                'vehicle_id': info.vehicle_id,
                'position_x': info.position_x,
                'position_y': info.position_y,
                'speed': info.speed,
                'angle': info.angle,
                'road_id': info.road_id,
                'lane_index': info.lane_index
            }
        elif response.HasField('step') and not response.step.success:
            # Error case
            raise ValueError(response.step.error)
        else:
            raise ValueError("Unexpected response type")


def main():
    """Example usage of the Artery control client."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Artery External Control Client')
    parser.add_argument('--host', default='localhost', help='Server hostname')
    parser.add_argument('--port', type=int, default=8888, help='Server port')
    parser.add_argument('command', choices=['step', 'time', 'status', 'vehicles', 'info'],
                       help='Command to execute')
    parser.add_argument('--count', type=int, default=1, help='Number of steps (for step command)')
    parser.add_argument('--vehicle-id', help='Vehicle ID (for info command)')
    
    args = parser.parse_args()
    
    client = ArteryControlClient(args.host, args.port)
    
    try:
        if args.command == 'step':
            result = client.step(args.count)
            if result['success']:
                print(f"Stepped {args.count} time(s) to time: {result['current_time']:.3f}s")
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")
        
        elif args.command == 'time':
            time = client.get_time()
            print(f"Current simulation time: {time:.3f}s")
        
        elif args.command == 'status':
            status = client.get_status()
            print(f"Status:")
            print(f"  Connected: {status['connected']}")
            print(f"  Time: {status['time']:.3f}s")
            print(f"  Step interval: {status['step_interval']:.3f}s")
            print(f"  Vehicles: {status['vehicle_count']}")
        
        elif args.command == 'vehicles':
            vehicles = client.get_vehicles()
            print(f"Vehicles ({len(vehicles)}):")
            for vid in vehicles:
                print(f"  {vid}")
        
        elif args.command == 'info':
            if not args.vehicle_id:
                print("Error: --vehicle-id required for info command")
                sys.exit(1)
            info = client.get_vehicle_info(args.vehicle_id)
            print(f"Vehicle {info['vehicle_id']}:")
            print(f"  Position: ({info['position_x']:.2f}, {info['position_y']:.2f}) m")
            print(f"  Speed: {info['speed']:.2f} m/s")
            print(f"  Angle: {info['angle']:.2f}°")
            print(f"  Road: {info['road_id']}")
            print(f"  Lane: {info['lane_index']}")
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
