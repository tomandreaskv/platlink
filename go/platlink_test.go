package platlink_test

import (
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"platlink"
)

type protocolSpec struct {
	TestVectors []testVector `json:"test_vectors"`
}

type testVector struct {
	Name         string `json:"name"`
	Hex          string `json:"hex"`
	Valid        bool   `json:"valid"`
	TypeID       string `json:"type_id"`
	SubID        string `json:"sub_id"`
	Format       string `json:"format"`
	Category     string `json:"category"`
	DecodedValue any    `json:"decoded_value"`
}

func parseHexByte(t *testing.T, s string) byte {
	t.Helper()
	cleaned := strings.TrimPrefix(strings.ToLower(s), "0x")
	v, err := strconv.ParseUint(cleaned, 16, 8)
	if err != nil {
		t.Fatalf("klarte ikke parse hex-byte %q: %v", s, err)
	}
	return byte(v)
}

func TestProtocolVectorsFromJSON(t *testing.T) {
	specPath := filepath.Join("..", "protocol_v1.json")
	rawJSON, err := os.ReadFile(specPath)
	if err != nil {
		t.Fatalf("klarte ikke lese %s: %v", specPath, err)
	}

	var spec protocolSpec
	if err := json.Unmarshal(rawJSON, &spec); err != nil {
		t.Fatalf("klarte ikke parse JSON: %v", err)
	}

	if len(spec.TestVectors) == 0 {
		t.Fatal("ingen testvektorer funnet i protocol_v1.json")
	}

	for _, tv := range spec.TestVectors {
		t.Run(tv.Name, func(t *testing.T) {
			pktBytes, err := hex.DecodeString(tv.Hex)
			if err != nil {
				t.Fatalf("ugyldig hex i testvektor %q: %v", tv.Hex, err)
			}

			valErr := platlink.ValidateBytes(pktBytes)
			pkt, parseErr := platlink.FromBytes(pktBytes)

			if !tv.Valid {
				if valErr == nil || parseErr == nil {
					t.Fatalf("forventet feil for ugyldig testvektor %q, men fikk ingen feil", tv.Name)
				}
				return
			}

			if valErr != nil || parseErr != nil {
				t.Fatalf("forventet gyldig pakke for %q, men fikk valErr=%v, parseErr=%v", tv.Name, valErr, parseErr)
			}

			wantTypeID := parseHexByte(t, tv.TypeID)
			wantSubID := parseHexByte(t, tv.SubID)
			wantFmt := parseHexByte(t, tv.Format)
			wantCat := parseHexByte(t, tv.Category)

			if pkt.TypeID != wantTypeID {
				t.Errorf("TypeID = 0x%02X, forventet 0x%02X", pkt.TypeID, wantTypeID)
			}
			if pkt.SubID != wantSubID {
				t.Errorf("SubID = 0x%02X, forventet 0x%02X", pkt.SubID, wantSubID)
			}
			if pkt.Format() != wantFmt {
				t.Errorf("Format = 0x%X, forventet 0x%X", pkt.Format(), wantFmt)
			}
			if pkt.Category() != wantCat {
				t.Errorf("Category = 0x%X, forventet 0x%X", pkt.Category(), wantCat)
			}

			switch pkt.Format() {
			case platlink.FmtFixed32:
				wantFloat, ok := tv.DecodedValue.(float64)
				if !ok {
					t.Fatalf("forventet numerisk decoded_value for Fixed32, fikk %T", tv.DecodedValue)
				}
				gotFloat := pkt.Fixed32Float()
				if math.Abs(gotFloat-wantFloat) > 1e-9 {
					t.Errorf("Fixed32Float = %v, forventet %v", gotFloat, wantFloat)
				}
			case platlink.FmtRaw32, platlink.FmtSystem:
				wantNum, ok := tv.DecodedValue.(float64)
				if !ok {
					t.Fatalf("forventet numerisk decoded_value, fikk %T", tv.DecodedValue)
				}
				if pkt.Uint32() != uint32(wantNum) {
					t.Errorf("Uint32 = %d, forventet %d", pkt.Uint32(), uint32(wantNum))
				}
			case platlink.FmtDigital:
				wantBool, ok := tv.DecodedValue.(bool)
				if !ok {
					t.Fatalf("forventet boolsk decoded_value for Digital, fikk %T", tv.DecodedValue)
				}
				if pkt.Digital() != wantBool {
					t.Errorf("Digital = %v, forventet %v", pkt.Digital(), wantBool)
				}
			}

			roundtrip := pkt.ToBytes()
			if hex.EncodeToString(roundtrip) != strings.ToUpper(tv.Hex) && hex.EncodeToString(roundtrip) != strings.ToLower(tv.Hex) {
				t.Errorf("roundtrip hex = %X, forventet %s", roundtrip, tv.Hex)
			}
		})
	}
}

func TestCreatorsAndHelpers(t *testing.T) {
	pkt := platlink.CreateFixed32Scaled(platlink.CatTemperature, 0x01, 2253)
	raw := pkt.ToBytes()
	if err := platlink.ValidateBytes(raw); err != nil {
		t.Fatalf("uventet valideringsfeil: %v", err)
	}
	if pkt.Fixed32Float() != 22.53 {
		t.Errorf("Fixed32Float = %v, forventet 22.53", pkt.Fixed32Float())
	}

	neg := platlink.CreateFixed32Scaled(platlink.CatTemperature, 0x01, -1050)
	if neg.Fixed32Float() != -10.5 {
		t.Errorf("Fixed32Float = %v, forventet -10.5", neg.Fixed32Float())
	}

	dig := platlink.CreateDigital(platlink.CatSystem, 0x05, true)
	if !dig.Digital() {
		t.Error("forventet Digital() == true")
	}
}
