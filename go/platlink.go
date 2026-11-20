// Package platlink implementerer koding, dekoding og validering av PlatLink v1.0
// binærpakker (fast 8-byte ramme med 0xA5-header og CRC-8 med polynom 0x07).
// Pakken er helt avhengighetsfri og bruker kun Go sitt standardbibliotek.
package platlink

import (
	"encoding/binary"
	"errors"
	"fmt"
)

// Protokollkonstanter for PlatLink v1.0.
const (
	Header         byte    = 0xA5 // Fast startbyte for rammesynkronisering
	PacketSize     int     = 8    // Fast pakkestørrelse i byte
	PayloadSize    int     = 4    // Fast nyttelaststørrelse i byte (big-endian)
	CRCPolynomial  byte    = 0x07 // CRC-8 polynom (x^8 + x^2 + x + 1)
	CRCInit        byte    = 0x00 // Startverdi for CRC-8 beregning
	Fixed32Scale   float64 = 100.0
	Fixed32IntBase int32   = 100
)

// Formater (høy nibble i TypeID: bits 7..4).
const (
	FmtDigital byte = 0x1 // Boolsk tilstand (0 = false, ellers true i payload[3])
	FmtAnalog8 byte = 0x2 // Usignert 8-bit verdi (0..255 i payload[3])
	FmtFixed32 byte = 0x3 // Signert 32-bit heltall delt på 100 (Fixed32)
	FmtRaw32   byte = 0x4 // Usignert 32-bit heltall (uint32)
	FmtInt32   byte = 0x5 // Signert 32-bit heltall (int32)
	FmtSystem  byte = 0xF // System- og kontrollmeldinger (0xF0..0xFF)
)

// Kategorier (lav nibble i TypeID: bits 3..0).
const (
	CatTemperature byte = 0x1 // Temperatur (°C)
	CatHumidity    byte = 0x2 // Fuktighet (%)
	CatLight       byte = 0x3 // Lys (lux)
	CatPH          byte = 0x4 // pH-verdi
	CatEC          byte = 0x5 // Elektrisk konduktivitet (µS/cm)
	CatWaterLevel  byte = 0x6 // Vannstand (cm)
	CatPressure    byte = 0x7 // Trykk (hPa)
	CatCustom      byte = 0xE // Reservert for egne utvidelser
	CatSystem      byte = 0xF // Systemkategori
)

// Systemmeldinger (TypeID i området 0xF0..0xFF).
const (
	SysHeartbeat byte = 0xF0 // Periodisk livstegn med oppetid i sekunder (uint32)
	SysDiscovery byte = 0xF4 // Node-annonsering / kapabiliteter
	SysOTACtrl   byte = 0xFC // OTA-kontroll (SubID angir underkommando)
	SysOTAData   byte = 0xFD // OTA-firmwaredata (SubID = chunk/sekvens, 4B data)
	SysReset     byte = 0xFF // Tilbakestillingskommando
)

// OTA-underkommandoer for SubID når TypeID er SysOTACtrl (0xFC).
const (
	OTACmdInit   byte = 0x01 // Start OTA-økt; payload = firmwarestørrelse i byte
	OTACmdVerify byte = 0x03 // Verifiser OTA-overføring; payload = forventet CRC32
)

// Vanlige feil ved validering og parsing av PlatLink-pakker.
var (
	ErrInvalidLength = errors.New("platlink: pakke må være nøyaktig 8 byte")
	ErrInvalidHeader = errors.New("platlink: ugyldig header (forventet 0xA5)")
	ErrInvalidCRC    = errors.New("platlink: ugyldig CRC-8 sjekksum")
)

// Packet representerer en dekodet eller ferdigpakket 8-byte PlatLink-ramme.
type Packet struct {
	Header  byte
	TypeID  byte
	SubID   byte
	Payload [PayloadSize]byte
	CRC8    byte
}

// MakeTypeID setter sammen format-nibble (høy nibble) og kategori-nibble (lav nibble).
func MakeTypeID(format, category byte) byte {
	return ((format & 0x0F) << 4) | (category & 0x0F)
}

// SplitTypeID deler TypeID opp i (format, category).
func SplitTypeID(typeID byte) (format, category byte) {
	return (typeID >> 4) & 0x0F, typeID & 0x0F
}

// CRC8 beregner CRC-8 over data med polynom 0x07 og startverdi 0x00.
func CRC8(data []byte) byte {
	crc := CRCInit
	for _, b := range data {
		crc ^= b
		for bit := 0; bit < 8; bit++ {
			if (crc & 0x80) != 0 {
				crc = (crc << 1) ^ CRCPolynomial
			} else {
				crc <<= 1
			}
		}
	}
	return crc
}

// Pack bygger en komplett Packet fra typeID, subID og 4-byte payload, og beregner CRC-8.
func Pack(typeID, subID byte, payload [PayloadSize]byte) Packet {
	pkt := Packet{
		Header:  Header,
		TypeID:  typeID,
		SubID:   subID,
		Payload: payload,
	}
	raw := pkt.toBytesWithoutCRC()
	pkt.CRC8 = CRC8(raw[:PacketSize-1])
	return pkt
}

// CreateUint32 oppretter en pakke med en usignert 32-bit verdi i big-endian.
func CreateUint32(typeID, subID byte, value uint32) Packet {
	var p [PayloadSize]byte
	binary.BigEndian.PutUint32(p[:], value)
	return Pack(typeID, subID, p)
}

// CreateInt32 oppretter en pakke med et signert 32-bit heltall i big-endian.
func CreateInt32(typeID, subID byte, value int32) Packet {
	return CreateUint32(typeID, subID, uint32(value))
}

// CreateFixed32Scaled oppretter en Fixed32-pakke (format 0x3) fra en skalert verdi (* 100).
func CreateFixed32Scaled(category, subID byte, scaledBy100 int32) Packet {
	typeID := MakeTypeID(FmtFixed32, category)
	return CreateInt32(typeID, subID, scaledBy100)
}

// CreateDigital oppretter en Digital-pakke (format 0x1) der payload[3] er 1 for true og 0 for false.
func CreateDigital(category, subID byte, state bool) Packet {
	typeID := MakeTypeID(FmtDigital, category)
	var v uint32
	if state {
		v = 1
	}
	return CreateUint32(typeID, subID, v)
}

// CreateAnalog8 oppretter en Analog8-pakke (format 0x2) med verdi 0..255 i payload[3].
func CreateAnalog8(category, subID byte, value byte) Packet {
	typeID := MakeTypeID(FmtAnalog8, category)
	return CreateUint32(typeID, subID, uint32(value))
}

// ValidateBytes sjekker at rådata har lengde 8, header 0xA5 og gyldig CRC-8.
func ValidateBytes(raw []byte) error {
	if len(raw) != PacketSize {
		return ErrInvalidLength
	}
	if raw[0] != Header {
		return ErrInvalidHeader
	}
	expected := CRC8(raw[:PacketSize-1])
	if expected != raw[PacketSize-1] {
		return fmt.Errorf("%w: beregnet 0x%02X, fikk 0x%02X", ErrInvalidCRC, expected, raw[PacketSize-1])
	}
	return nil
}

// FromBytes validerer og parser en 8-byte ramme til en Packet.
func FromBytes(raw []byte) (Packet, error) {
	if err := ValidateBytes(raw); err != nil {
		return Packet{}, err
	}
	var pkt Packet
	pkt.Header = raw[0]
	pkt.TypeID = raw[1]
	pkt.SubID = raw[2]
	copy(pkt.Payload[:], raw[3:7])
	pkt.CRC8 = raw[7]
	return pkt, nil
}

func (p Packet) toBytesWithoutCRC() [PacketSize]byte {
	var out [PacketSize]byte
	out[0] = p.Header
	out[1] = p.TypeID
	out[2] = p.SubID
	copy(out[3:7], p.Payload[:])
	out[7] = p.CRC8
	return out
}

// ToBytes serialiserer pakken til en 8-byte slice i trådrekkefølge.
func (p Packet) ToBytes() []byte {
	out := make([]byte, PacketSize)
	out[0] = p.Header
	out[1] = p.TypeID
	out[2] = p.SubID
	copy(out[3:7], p.Payload[:])
	out[7] = p.CRC8
	return out
}

// Uint32 dekoder 4-byte payload som et usignert 32-bit heltall (big-endian).
func (p Packet) Uint32() uint32 {
	return binary.BigEndian.Uint32(p.Payload[:])
}

// Int32 dekoder 4-byte payload som et signert 32-bit heltall (big-endian).
func (p Packet) Int32() int32 {
	return int32(p.Uint32())
}

// Fixed32Scaled returnerer Fixed32-verdien som heltall skalert med 100 (f.eks. 2253 for 22.53).
func (p Packet) Fixed32Scaled() int32 {
	return p.Int32()
}

// Fixed32Float dekoder Fixed32-verdien som float64 (int32 / 100.0).
func (p Packet) Fixed32Float() float64 {
	return float64(p.Int32()) / Fixed32Scale
}

// Digital dekoder boolsk verdi fra payload[3] (0 = false, ellers true).
func (p Packet) Digital() bool {
	return p.Payload[3] != 0
}

// Analog8 dekoder usignert 8-bit verdi fra payload[3].
func (p Packet) Analog8() byte {
	return p.Payload[3]
}

// Format returnerer høy nibble (formatkode) fra TypeID.
func (p Packet) Format() byte {
	f, _ := SplitTypeID(p.TypeID)
	return f
}

// Category returnerer lav nibble (kategorikode) fra TypeID.
func (p Packet) Category() byte {
	_, c := SplitTypeID(p.TypeID)
	return c
}
