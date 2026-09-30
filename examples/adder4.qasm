// 4-bit ripple-carry adder core (Cuccaro-style MAJ/UMA with Toffolis), 8 qubits.
// Qiskit decomposes each ccx into Clifford+T gates when the config loads this file.
OPENQASM 2.0;
include "qelib1.inc";
qreg q[8];
creg c[8];
// a = q0,q2,q4,q6 ; b = q1,q3,q5,q7
x q[0];
x q[3];
cx q[2], q[1];
cx q[2], q[0];
ccx q[0], q[1], q[2];
cx q[4], q[3];
cx q[4], q[2];
ccx q[2], q[3], q[4];
cx q[6], q[5];
cx q[6], q[4];
ccx q[4], q[5], q[6];
ccx q[4], q[5], q[6];
cx q[6], q[4];
cx q[4], q[5];
ccx q[2], q[3], q[4];
cx q[4], q[2];
cx q[2], q[3];
ccx q[0], q[1], q[2];
cx q[2], q[0];
cx q[0], q[1];
barrier q;
measure q -> c;
