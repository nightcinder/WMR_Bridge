/*
 * Host tests for the FF17 control and status v2 characteristic:
 * command parsing, status packing and unpacking, golden bytes, and result mapping.
 */

#include <stdio.h>
#include <string.h>
#include "wmr_ctrl.h"
#include "wmr_controller.h"

static int checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)){ failures++; \
    printf("  FAIL line %d: %s\n", __LINE__, #cond); } } while (0)

static void test_command_parsing(void){
    printf("FF17 command parsing\n");
    uint8_t op = 0, hand = 0;

    /* OP_DISCOVER: 1 byte or 2 bytes */
    const uint8_t disc1[1] = {0x01};
    CHECK(wmr_ctrl_parse_command(disc1, 1, &op, &hand));
    CHECK(op == WMR_CTRL_OP_DISCOVER && hand == 0);

    const uint8_t disc2[2] = {0x01, 0xFF};
    CHECK(wmr_ctrl_parse_command(disc2, 2, &op, &hand));
    CHECK(op == WMR_CTRL_OP_DISCOVER && hand == 0);

    /* OP_FORGET_ADDRS: 1 byte or 2 bytes */
    const uint8_t forget1[1] = {0x04};
    CHECK(wmr_ctrl_parse_command(forget1, 1, &op, &hand));
    CHECK(op == WMR_CTRL_OP_FORGET_ADDRS && hand == 0);

    /* OP_CONNECT: requires 2 bytes and valid hand */
    const uint8_t conn_left[2] = {0x02, 0x00};
    CHECK(wmr_ctrl_parse_command(conn_left, 2, &op, &hand));
    CHECK(op == WMR_CTRL_OP_CONNECT && hand == 0);

    const uint8_t conn_right[2] = {0x02, 0x01};
    CHECK(wmr_ctrl_parse_command(conn_right, 2, &op, &hand));
    CHECK(op == WMR_CTRL_OP_CONNECT && hand == 1);

    const uint8_t conn_bad_hand[2] = {0x02, 0x02};
    CHECK(!wmr_ctrl_parse_command(conn_bad_hand, 2, &op, &hand));

    const uint8_t conn_short[1] = {0x02};
    CHECK(!wmr_ctrl_parse_command(conn_short, 1, &op, &hand));

    /* OP_DISCONNECT: requires 2 bytes and valid hand */
    const uint8_t disc_left[2] = {0x03, 0x00};
    CHECK(wmr_ctrl_parse_command(disc_left, 2, &op, &hand));
    CHECK(op == WMR_CTRL_OP_DISCONNECT && hand == 0);

    const uint8_t disc_right[2] = {0x03, 0x01};
    CHECK(wmr_ctrl_parse_command(disc_right, 2, &op, &hand));
    CHECK(op == WMR_CTRL_OP_DISCONNECT && hand == 1);

    const uint8_t disc_bad_hand[2] = {0x03, 0x05};
    CHECK(!wmr_ctrl_parse_command(disc_bad_hand, 2, &op, &hand));

    /* OP_FORGET_BOND: requires 2 bytes and valid hand */
    const uint8_t bond_left[2] = {0x05, 0x00};
    CHECK(wmr_ctrl_parse_command(bond_left, 2, &op, &hand));
    CHECK(op == WMR_CTRL_OP_FORGET_BOND && hand == 0);

    const uint8_t bond_right[2] = {0x05, 0x01};
    CHECK(wmr_ctrl_parse_command(bond_right, 2, &op, &hand));
    CHECK(op == WMR_CTRL_OP_FORGET_BOND && hand == 1);

    /* Invalid opcodes and edge cases */
    const uint8_t invalid_op[1] = {0x06};
    CHECK(!wmr_ctrl_parse_command(invalid_op, 1, &op, &hand));

    const uint8_t too_long[3] = {0x01, 0x00, 0x00};
    CHECK(!wmr_ctrl_parse_command(too_long, 3, &op, &hand));

    CHECK(!wmr_ctrl_parse_command(NULL, 1, &op, &hand));
    CHECK(!wmr_ctrl_parse_command(disc1, 0, &op, &hand));
}

static void test_status_pack_unpack(void){
    printf("FF17 status v2 packing, unpacking, and golden bytes\n");

    wmr_ctrl_status_t s;
    memset(&s, 0, sizeof(s));
    s.version = WMR_CTRL_VERSION;

    /* Left hand */
    s.hands[WMR_HAND_LEFT].connected = true;
    s.hands[WMR_HAND_LEFT].connecting = false;
    s.hands[WMR_HAND_LEFT].descriptor_available = true;
    s.hands[WMR_HAND_LEFT].address_source = 2; /* stored */
    s.hands[WMR_HAND_LEFT].model = 0;          /* WMR */
    s.hands[WMR_HAND_LEFT].address[3] = 0xAA;
    s.hands[WMR_HAND_LEFT].address[4] = 0xBB;
    s.hands[WMR_HAND_LEFT].address[5] = 0xCC;

    /* Right hand */
    s.hands[WMR_HAND_RIGHT].connected = false;
    s.hands[WMR_HAND_RIGHT].connecting = true;
    s.hands[WMR_HAND_RIGHT].descriptor_available = false;
    s.hands[WMR_HAND_RIGHT].address_source = 1; /* override */
    s.hands[WMR_HAND_RIGHT].model = 1;          /* PSVR2 */
    s.hands[WMR_HAND_RIGHT].address[3] = 0x11;
    s.hands[WMR_HAND_RIGHT].address[4] = 0x22;
    s.hands[WMR_HAND_RIGHT].address[5] = 0x33;

    /* Global */
    s.discovery_state = WMR_CTRL_DISC_INQUIRY; /* 1 */
    s.discovery_round = 2;
    s.last_command = WMR_CTRL_OP_CONNECT;     /* 2 */
    s.last_result = WMR_CTRL_RES_BUSY;        /* 1 */

    static const uint8_t golden[WMR_CTRL_STATUS_SIZE] = {
        0x01,                               /* version 1 */
        /* LEFT: flags (connected=bit0, desc=bit2 -> 0x05), src=2, model=0, tail=AA BB CC, res=0 */
        0x05, 0x02, 0x00, 0xAA, 0xBB, 0xCC, 0x00,
        /* RIGHT: flags (connecting=bit1 -> 0x02), src=1, model=1, tail=11 22 33, res=0 */
        0x02, 0x01, 0x01, 0x11, 0x22, 0x33, 0x00,
        /* Global: disc_state=1, disc_round=2, last_cmd=2, last_res=1, res=0 */
        0x01, 0x02, 0x02, 0x01, 0x00
    };

    uint8_t packed[WMR_CTRL_STATUS_SIZE];
    memset(packed, 0xFF, sizeof(packed));
    wmr_ctrl_pack_status(&s, packed);

    CHECK(sizeof(golden) == 20);
    CHECK(WMR_CTRL_STATUS_SIZE == 20);
    CHECK(WMR_CTRL_MIN_MTU == 23);
    CHECK(memcmp(packed, golden, sizeof(golden)) == 0);

    wmr_ctrl_status_t back;
    CHECK(wmr_ctrl_unpack_status(golden, sizeof(golden), &back));
    CHECK(back.version == 1);
    CHECK(back.hands[WMR_HAND_LEFT].connected == true);
    CHECK(back.hands[WMR_HAND_LEFT].connecting == false);
    CHECK(back.hands[WMR_HAND_LEFT].descriptor_available == true);
    CHECK(back.hands[WMR_HAND_LEFT].address_source == 2);
    CHECK(back.hands[WMR_HAND_LEFT].model == 0);
    CHECK(back.hands[WMR_HAND_LEFT].address[3] == 0xAA);
    CHECK(back.hands[WMR_HAND_LEFT].address[4] == 0xBB);
    CHECK(back.hands[WMR_HAND_LEFT].address[5] == 0xCC);

    CHECK(back.hands[WMR_HAND_RIGHT].connected == false);
    CHECK(back.hands[WMR_HAND_RIGHT].connecting == true);
    CHECK(back.hands[WMR_HAND_RIGHT].descriptor_available == false);
    CHECK(back.hands[WMR_HAND_RIGHT].address_source == 1);
    CHECK(back.hands[WMR_HAND_RIGHT].model == 1);
    CHECK(back.hands[WMR_HAND_RIGHT].address[3] == 0x11);
    CHECK(back.hands[WMR_HAND_RIGHT].address[4] == 0x22);
    CHECK(back.hands[WMR_HAND_RIGHT].address[5] == 0x33);

    CHECK(back.discovery_state == WMR_CTRL_DISC_INQUIRY);
    CHECK(back.discovery_round == 2);
    CHECK(back.last_command == WMR_CTRL_OP_CONNECT);
    CHECK(back.last_result == WMR_CTRL_RES_BUSY);

    /* Error handling */
    CHECK(!wmr_ctrl_unpack_status(golden, 19, &back));
    CHECK(!wmr_ctrl_unpack_status(NULL, 20, &back));

    uint8_t bad_version[20];
    memcpy(bad_version, golden, 20);
    bad_version[0] = 2;
    CHECK(!wmr_ctrl_unpack_status(bad_version, 20, &back));
}

static void test_result_mapping(void){
    printf("FF17 result to ATT error mapping\n");
    CHECK(wmr_ctrl_res_to_att_error(WMR_CTRL_RES_OK) == 0);
    CHECK(wmr_ctrl_res_to_att_error(WMR_CTRL_RES_BUSY) == 0x81);
    CHECK(wmr_ctrl_res_to_att_error(WMR_CTRL_RES_NO_ADDRESS) == 0x82);
    CHECK(wmr_ctrl_res_to_att_error(WMR_CTRL_RES_NOTHING_TO_DO) == 0x83);
    CHECK(wmr_ctrl_res_to_att_error(WMR_CTRL_RES_NOT_CONNECTED) == 0x84);
    CHECK(wmr_ctrl_res_to_att_error(WMR_CTRL_RES_NOT_ENABLED) == 0x85);
    CHECK(wmr_ctrl_res_to_att_error(WMR_CTRL_RES_INVALID_PARAM) == 0x86);
}

static void test_connect_gate(void){
    printf("FF17 CONNECT OK-vs-BUSY for an already tracked hand\n");
    CHECK(wmr_controller_connect_gate(false, false) == WMR_CONNECT_GATE_START);
    CHECK(wmr_controller_connect_gate(false, true) == WMR_CONNECT_GATE_START);
    CHECK(wmr_controller_connect_gate(true, false) == WMR_CONNECT_GATE_ACK);
    CHECK(wmr_controller_connect_gate(true, true) == WMR_CONNECT_GATE_CLOSING);
}

int main(void){
    test_command_parsing();
    test_status_pack_unpack();
    test_result_mapping();
    test_connect_gate();
    printf("\n%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
