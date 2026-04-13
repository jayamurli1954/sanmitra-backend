"""
Panchang Constants
"""

NAKSHATRAS = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni",
    "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha",
    "Anuradha", "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha",
    "Shravana", "Dhanishta", "Shatabhisha", "Purva Bhadrapada",
    "Uttara Bhadrapada", "Revati"
]

TITHIS = [
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Purnima"
]

YOGAS = [
    "Vishkambha", "Priti", "Ayushman", "Saubhagya", "Shobhana",
    "Atiganda", "Sukarma", "Dhriti", "Shoola", "Ganda", "Vriddhi",
    "Dhruva", "Vyaghata", "Harshana", "Vajra", "Siddhi", "Vyatipata",
    "Variyan", "Parigha", "Shiva", "Siddha", "Sadhya", "Shubha",
    "Shukla", "Brahma", "Indra", "Vaidhriti"
]

KARANAS = [
    "Bava", "Balava", "Kaulava", "Taitila", "Garaja", "Vanija",
    "Vishti", "Shakuni", "Chatushpada", "Naga", "Kimstughna"
]

RASHIS = [
    "Mesha", "Vrishabha", "Mithuna", "Karka", "Simha", "Kanya",
    "Tula", "Vrishchika", "Dhanu", "Makara", "Kumbha", "Meena"
]

RUTHUS = ["Vasanta", "Grishma", "Varsha", "Sharad", "Hemanta", "Shishira"]

SAMVATSARAS = [
    "Prabhava", "Vibhava", "Shukla", "Pramoda", "Prajapati", "Angirasa",
    "Shrimukha", "Bhava", "Yuvan", "Dhatri", "Ishvara", "Bahudhanya",
    "Pramathi", "Vikrama", "Vrisha", "Chitrabhanu", "Svabhanu", "Tarana",
    "Parthiva", "Vyaya", "Sarvajit", "Sarvadharin", "Virodhin", "Vikrita",
    "Khara", "Nandana", "Vijaya", "Jaya", "Manmatha", "Durmukha",
    "Hemalamba", "Vilamba", "Vikarin", "Sharvari", "Plava", "Shubhakrit",
    "Shobhana", "Krodhin", "Vishvavasu", "Parabhava", "Plavanga", "Kilaka",
    "Saumya", "Sadharana", "Virodhikrit", "Paridhavi", "Pramadin", "Ananda",
    "Rakshasa", "Nala", "Pingala", "Kalayukta", "Siddharthi", "Raudra",
    "Durmathi", "Dundubhi", "Rudhirodgari", "Raktaksha", "Krodhana", "Kshaya"
]

VARAS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
VARA_SANSKRIT = ["रविवार", "सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार"]
VARA_DEITIES = [
    "Surya (Sun)", "Chandra (Moon)", "Mangal (Mars)", "Budh (Mercury)",
    "Brihaspati (Jupiter)", "Shukra (Venus)", "Shani (Saturn)"
]

NAKSHATRA_VARJYAM_STARTS = [
    50, 24, 30, 40, 14, 18, 30, 20, 32, 30,
    20, 18, 21, 20, 14, 14, 10, 14, 56, 24,
    20, 10, 10, 18, 16, 24, 30
]

NAKSHATRA_AMRITA_STARTS = [
    92, 66, 72, 82, 56, 53, 72, 62, 74, 72,
    62, 60, 63, 62, 56, 56, 52, 56, 98, 66,
    62, 52, 52, 60, 58, 66, 72
]

DUR_MUHURTA_INDICES = {
    0: [13],  # Sunday: 14th Muhurta (Index 13)
    1: [8, 11],  # Monday: 9th and 12th (Indices 8, 11)
    2: [1, 13],  # Tuesday: 2nd and 14th (Index 1, 13)
    3: [7],  # Wednesday: calibrated to midday slot (Drik-aligned)
    4: [7],  # Thursday: calibrated to midday slot (Drik-aligned)
    5: [2, 8],  # Friday: 3rd and 9th (Indices 2, 8)
    6: [0],  # Saturday: 1st (Index 0)
}
