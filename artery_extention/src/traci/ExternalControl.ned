package traci;

simple ExternalControl
{
    parameters:
        @class(ExternalControl);
        string coreModule = default(".traci.core");
        int port = default(8888);
}

