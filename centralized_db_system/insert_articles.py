import sqlite3

conn = sqlite3.connect('centralized_db.sqlite3')

data = [
('Aster','100 (One in a dent)','DB BS','1+2','224 X 244','46 X 69','Sheet Sets','Pigment',18,3,1049.0,1049.0,719.31,0.28,625.49),
('Bluemen','104 (One in a dent)','SB BS','1+1','140 X 224','46 X 69','Sheet Sets','Pigment',24,3,799.0,799.0,532.67,0.3,451.41),
('Bluemen','104 (One in a dent)','DB BS','1+2','224 X 254','46 X 69','Sheet Sets','Pigment',18,3,1299.0,1299.0,866.0,0.3,733.9),
('Cardinal','120','SB BS','1+1','150 X 224','46 X 69','Sheet Sets','Pigment',24,3,949.0,949.0,632.67,0.3,536.16),
('Cardinal','120','DB BS','1+2','224 X 254','46 X 69','Sheet Sets','Pigment',18,3,1499.0,1499.0,999.33,0.3,846.89),
('Cardinal','120','KS BS','1+2','274 X 274','46 X 69','Sheet Sets','Pigment',12,3,1899.0,1899.0,1266.0,0.3,1072.88),
('Epigram','120','SB BS','1+1','150 X 224','46 X 69','Sheet Sets','Pigment',24,3,1665.0,999.0,666.0,0.3,564.41),
('Epigram','120','DB BS','1+2','224 X 254','46 X 69','Sheet Sets','Pigment',18,3,2799.0,1679.4,1119.6,0.3,948.81),
('Epigram','120','KS BS','1+2','274 X 274','46 X 69','Sheet Sets','Pigment',12,3,3199.0,1919.4,1279.6,0.3,1084.41),
('Floral Fiesta','140','SB BS','1+1','150 X 228','46 X 69','Sheet Sets','Pigment',24,3,1099.0,1099.0,732.67,0.3,620.9),
('Floral Fiesta','140','DB BS','1+2','228 X 254','46 X 69','Sheet Sets','Pigment',18,3,1799.0,1799.0,1199.33,0.3,1016.38),
('Floral Fiesta','140','KS BS','1+2','274 X 274','46 X 69','Sheet Sets','Pigment',12,3,1999.0,1999.0,1332.67,0.3,1129.38),
('Floral Fiesta','140','KB FS','1+2','183x198x30','46 X 69','KB Fitted Sheet','Pigment',12,3,2099.0,2099.0,1299.38,0.35,1101.17),
('Floral Fiesta','140','DB FS','1+2','152x198x30','46 X 69','DB Fitted Sheet','Pigment',12,3,1899.0,1899.0,1175.57,0.35,996.25),
('Florentine','144','DB BS','1+2','228 X 254','46 X 69','Sheet Sets','Pigment',12,3,3199.0,1919.4,1188.2,0.35,1006.95),
('Florentine','144','KS BS','1+2','274 X 274','46 X 69','Sheet Sets','Pigment',12,3,3799.0,2279.4,1411.06,0.35,1195.81),
('Florentine / Allure','144','KB FS','1+2','183x198x30','46 x 69','KB Fitted Sheet','Pigment',12,3,2199.0,2199.0,1361.29,0.35,1153.63),
('Florentine / Allure','144','DB FS','1+2','152x198x30','46 x 69','DB Fitted Sheet','Pigment',12,3,2099.0,2099.0,1299.38,0.35,1101.17),
('Vintage','180','SB BS','2+2','152 X 228','46 x 69','Sheet Sets','Pigment',12,3,2299.0,2299.0,1313.71,0.4,1113.32),
('Vintage','180','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Pigment',12,3,2499.0,2499.0,1428.0,0.4,1210.17),
('525B','164','DB BS','1+2','229 x 274','46 x 69','Sheet Sets','Pigment',12,3,2349.0,2349.0,1454.14,0.35,1232.32),
('525B','164','KS BS','1+2','274 X 274','46 x 69','Sheet Sets','Pigment',12,3,2599.0,2599.0,1608.9,0.35,1363.48),
('Wonder Land- Kids','148','SB BS','1+1','152 X 228','46 x 69','Sheet Sets','Pigment',18,1,1449.0,1449.0,897.0,0.35,760.17),
('Wonder Land- Kids','148','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Pigment',12,1,2499.0,2499.0,1547.0,0.35,1311.02),
('Wonder Land- Kids','148','DB Comf','1','224 x 254','Wadding 150 Gsm','Comforter','Pigment',6,1,4999.0,4999.0,2753.69,0.35,2333.63),
('Sage','180','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Pigment',12,1,4359.0,2615.4,1619.06,0.35,1372.08),
('Thyme','210','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Pigment',12,1,4599.0,2759.4,1520.01,0.35,1288.14),
('Toiel','300','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Pigment',12,1,3699.0,3699.0,1880.85,0.4,1593.94),
('AKIRA','400','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Digital',6,1,6999.0,6999.0,3262.25,0.45,2764.62),
('Rigel Living','400','KS BS','1+4','274 x 274','46 x 69','Sheet Sets','Digital',6,1,7399.0,7399.0,3448.69,0.45,2922.62),
('Ethnicity','300','KS BS','1+4','274 x 274','46 x 69','Sheet Sets','Digital',6,1,7299.0,7299.0,3402.08,0.45,2883.12),
('Grid Space','180','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Digital',12,1,3999.0,3999.0,2033.39,0.4,1723.21),
('Bela Twill','220','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Digital',12,1,4599.0,4599.0,2338.47,0.4,1981.76),
('Celebareting India','300','KS BS','1+4','274 x 274','46 x 69','Sheet Sets','Digital',12,1,6449.0,6449.0,3005.89,0.45,2547.36),
('Cotton Comforts','180','SB BS','2+2','150 x 274','46 x 69','Sheet Sets','Bleached',12,1,2499.0,2499.0,1547.0,0.35,1311.02),
('Cotton Comforts','180','DBL BS','1+2','224 x 254','46 x 69','Sheet Sets','Bleached',12,1,2399.0,2399.0,1485.1,0.35,1258.56),
('Cotton Comforts','180','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Bleached',12,1,2499.0,2499.0,1547.0,0.35,1311.02),
('FLORA','120','DB BS','1+2','224 x 244','46 x 69','Sheet Sets','Bleached',12,1,1899.0,1899.0,1175.57,0.35,996.25),
('FLORA','120','SB BS','2+2','150 x 224','46 x 69','Sheet Sets','Bleached',12,1,2049.0,2049.0,1268.43,0.35,1074.94),
('FLORA','120','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','Bleached',12,1,2199.0,2199.0,1361.29,0.35,1153.63),
('Beaucale','164','SB BS','2+2','150 x 274','46 x 69','Sheet Sets','DYED',12,5,2999.0,2999.0,1651.99,0.35,1399.99),
('Beaucale','164','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','DYED',12,5,3099.0,3099.0,1707.08,0.35,1446.67),
('ECSTASY','400','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','DYED',12,7,4299.0,4299.0,2368.09,0.35,2006.86),
('Jade ( Dobby Stripe)','300','KS BS','1+2','274 x 274','46 x 69','Sheet Sets','DYED',12,6,3699.0,3699.0,2037.58,0.35,1726.77),
]

conn.executemany('''INSERT INTO article_master_v2 
(brand,tc,size,units,bs_size,pillow_size,product,print_style,bale_size,colors,mrp,selling_price,ptr,retailer_margin,exmill_price) 
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', data)

conn.commit()
print(f'Done! {len(data)} articles inserted!')
conn.close()