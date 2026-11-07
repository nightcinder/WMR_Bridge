/*
 * Copyright (C) 2017 BlueKitchen GmbH
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holders nor the names of
 *    contributors may be used to endorse or promote products derived
 *    from this software without specific prior written permission.
 * 4. Any redistribution, use, or modification is done solely for
 *    personal benefit and not for any commercial purpose or for
 *    monetary gain.
 *
 * THIS SOFTWARE IS PROVIDED BY BLUEKITCHEN GMBH AND CONTRIBUTORS
 * ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL BLUEKITCHEN
 * GMBH OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 * BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
 * OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF
 * THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 *
 * Please inquire about commercial licensing options at 
 * contact@bluekitchen-gmbh.com
 *
 */

/*
 * wmr_codec.h - wire format and frame constants shared by the codec modules.
 *
 * Standalone: no wmr_config.h and no BTstack dependency, so wmr_decode, wmr_decoded,
 * wmr_batch, wmr_protocol and wmr_hid build as an internal static library on the host.
 * Firmware-wide tunables stay in wmr_config.h, which includes this header.
 */

#ifndef WMR_CODEC_H
#define WMR_CODEC_H

#include <stdint.h>

/* ---- Hands ------------------------------------------------------------- */

#define WMR_HAND_LEFT        0
#define WMR_HAND_RIGHT       1
#define WMR_NUM_CONTROLLERS  2

/* ---- Frames ------------------------------------------------------------ */

/* Largest controller frame (A1 + report ID + payload) that is forwarded.
 * WMR frames are 46 bytes; PSVR2 input frames are up to 79. 80 is the smallest
 * value that carries both and still fits the static queue in ESP32 DRAM (96
 * overflows dram0_0_seg; see docs/PSVR2.md). Longer frames are counted as
 * drops. */
#ifndef WMR_CFG_MAX_FRAME
#define WMR_CFG_MAX_FRAME          80
#endif

/* PSVR2 HID transaction header, input report ID and largest complete frame (A1 + report
 * ID + payload). 0x31 reports carry 77 payload bytes, so the frame is 79 bytes. */
#define WMR_PSVR2_HID_HEADER       0xA1
#define WMR_PSVR2_INPUT_REPORT_ID  0x31
#define WMR_PSVR2_INPUT_FRAME_MAX  79
#if WMR_CFG_MAX_FRAME < WMR_PSVR2_INPUT_FRAME_MAX
#error "WMR_CFG_MAX_FRAME must be >= WMR_PSVR2_INPUT_FRAME_MAX (79)"
#endif

/* ---- Raw stream wire format -------------------------------------------- */

/* wire: size of the fragment header preceding each payload chunk. */
#define WMR_PROTO_HEADER_SIZE      12

/* wire: first byte of every notification ('R'). */
#define WMR_PROTO_MAGIC            0x52

/* Upper bound on one ATT notification value, whatever the MTU allows. */
#ifndef WMR_CFG_MAX_NOTIFICATION
#define WMR_CFG_MAX_NOTIFICATION   244
#endif

/* ATT opcode + handle overhead inside an ATT_MTU-sized PDU. */
#define WMR_ATT_NOTIFY_OVERHEAD    3

#endif /* WMR_CODEC_H */
